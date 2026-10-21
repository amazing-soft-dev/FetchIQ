import base64

import httpx
import pytest

from app.db import utcnow
from app.discovery import new_job_document
from app.enrichment import (
    EnrichmentService,
    format_utc_offset,
    parse_profile_emails,
    parse_utc_offset,
)
from app.export import build_row
from app.github import GitHubClient
from app.models import SearchCriteria


def _readme(text: str, ref: str) -> httpx.Response:
    encoded = base64.b64encode(text.encode()).decode()
    return httpx.Response(
        200,
        json={
            "type": "file",
            "encoding": "base64",
            "size": len(text),
            "content": encoded,
            "html_url": f"https://github.com/ada/ada/blob/{ref}/README.md",
        },
    )


def _profile(*, email, location: str, bio: str = "Mathematician") -> dict:
    return {
        "id": 7,
        "login": "ada",
        "name": "Ada Lovelace",
        "company": "Analytical Engines",
        "blog": "https://example.com",
        "location": location,
        "email": email,
        "bio": bio,
        "twitter_username": None,
        "created_at": "2012-01-01T00:00:00Z",
        "public_repos": 3,
    }


def _repos() -> list[dict]:
    return [
        {
            "name": "forked",
            "fork": True,
            "archived": False,
            "language": "Python",
            "topics": ["python"],
            "html_url": "https://github.com/ada/forked",
        },
        {
            "name": "old",
            "fork": False,
            "archived": True,
            "language": "Python",
            "topics": ["python"],
            "html_url": "https://github.com/ada/old",
        },
        {
            "name": "tools",
            "fork": False,
            "archived": False,
            "language": "Python",
            "topics": ["python"],
            "html_url": "https://github.com/ada/tools",
        },
        {
            "name": "notes",
            "fork": False,
            "archived": False,
            "language": "Ruby",
            "topics": [],
            "html_url": "https://github.com/ada/notes",
        },
    ]


async def _seed(db, *, keywords: list[str] | None = None) -> tuple[str, dict]:
    job = new_job_document(
        SearchCriteria(
            countries=["Germany"], keywords=keywords if keywords is not None else ["python"]
        )
    )
    job["status"] = "running"
    job["checkpoint"] = {"phase": "discovery_done", "windows": [], "last_enriched_user_id": None}
    await db.search_jobs.insert_one(job)
    candidate = {
        "job_id": job["_id"],
        "github_user_id": 7,
        "login": "ada",
        "profile_url": "https://github.com/ada",
        "enrichment_status": "pending",
        "collection_status": "discovered",
        "public_email": None,
    }
    await db.candidates.insert_one(candidate)
    return job["_id"], candidate


@pytest.mark.asyncio
async def test_enrichment_skips_forks_retries_master_and_keeps_published_email(db) -> None:
    calls: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        ref = request.url.params.get("ref")
        calls.append((path, ref))
        assert ".patch" not in path
        assert "/commits" not in path
        if path == "/users/ada":
            return httpx.Response(200, json=_profile(email=None, location="Remote"))
        if path == "/users/ada/repos":
            return httpx.Response(200, json=_repos())
        if path == "/repos/ada/ada/contents/README.md":
            if ref == "main":
                return httpx.Response(404, json={"message": "Not Found"})
            return _readme("Contact ada@example.com.", ref or "master")
        if path == "/repos/ada/tools/contents/README.md":
            return _readme("Python utilities.", ref or "main")
        if "/forked/" in path or "/old/" in path:
            raise AssertionError(path)
        if "/notes/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(404, json={"message": "Not Found"})

    job_id, _candidate = await _seed(db)
    github = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    outcome = await EnrichmentService(db, github).run(job_id)
    await github.aclose()

    assert outcome == "done"
    profile_refs = [ref for path, ref in calls if path == "/repos/ada/ada/contents/README.md"]
    assert profile_refs == ["main", "master"]
    assert any(path == "/repos/ada/tools/contents/README.md" for path, _ref in calls)
    stored = await db.candidates.find_one({"job_id": job_id, "github_user_id": 7})
    assert stored["public_email"] == "ada@example.com"
    assert stored["country"] is None
    assert stored["location_confidence"] == 0.0
    assert stored["primary_languages"] == ["Python"]
    assert "https://github.com/ada/tools" in stored["relevant_repository_urls"]
    assert "https://github.com/ada/forked" not in stored["relevant_repository_urls"]
    email = await db.contact_evidence.find_one({"github_user_id": 7, "kind": "email"})
    assert email["normalized_value"] == "ada@example.com"
    assert email["source_url"].endswith("/README.md")
    website = await db.contact_evidence.find_one({"github_user_id": 7, "kind": "website"})
    assert website["normalized_value"] == "https://example.com"


@pytest.mark.asyncio
async def test_repo_readme_email_is_kept_without_keywords(db) -> None:
    """A location-only search still reads repository READMEs for a published address."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/jscarton":
            return httpx.Response(
                200,
                json=_profile(
                    email=None,
                    location="Buenos Aires, Argentina",
                    bio=None,
                )
                | {
                    "login": "jscarton",
                    "name": "Juan Scarton",
                    "blog": "https://arepasapp.com",
                    "company": None,
                    "public_repos": 57,
                },
            )
        if path == "/users/jscarton/repos":
            repos = [
                {
                    "name": f"recent-{index}",
                    "fork": False,
                    "archived": False,
                    "language": "Go",
                    "topics": [],
                    "html_url": f"https://github.com/jscarton/recent-{index}",
                    "pushed_at": f"2024-12-{index:02d}T00:00:00Z",
                }
                for index in range(1, 12)
            ]
            repos.append(
                {
                    "name": "arepas-commander",
                    "fork": False,
                    "archived": False,
                    "language": "JavaScript",
                    "topics": [],
                    "html_url": "https://github.com/jscarton/arepas-commander",
                    "pushed_at": "2018-02-25T23:48:28Z",
                }
            )
            repos.append(
                {
                    "name": "forked",
                    "fork": True,
                    "archived": False,
                    "language": "JavaScript",
                    "topics": [],
                    "html_url": "https://github.com/jscarton/forked",
                    "pushed_at": "2025-01-01T00:00:00Z",
                }
            )
            return httpx.Response(200, json=repos)
        if path == "/repos/jscarton/arepas-commander/readme":
            return _readme("To contact me write me at <jscarton@gmail.com>", "HEAD")
        if "/forked/" in path:
            raise AssertionError(path)
        return httpx.Response(404, json={"message": "Not Found"})

    job_id, candidate = await _seed(db, keywords=[])
    await db.candidates.update_one(
        {"_id": candidate["_id"]},
        {"$set": {"login": "jscarton", "profile_url": "https://github.com/jscarton"}},
    )
    github = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    outcome = await EnrichmentService(db, github).run(job_id)
    await github.aclose()

    assert outcome == "done"
    stored = await db.candidates.find_one({"job_id": job_id, "github_user_id": 7})
    assert stored["name"] == "Juan Scarton"
    assert stored["country"] == "Argentina"
    assert stored["public_email"] == "jscarton@gmail.com"
    assert stored["collection_status"] == "enriched"
    email = await db.contact_evidence.find_one({"github_user_id": 7, "kind": "email"})
    assert email["normalized_value"] == "jscarton@gmail.com"
    assert email["source_url"].endswith("/README.md") or "arepas-commander" in email["source_url"]
    website = await db.contact_evidence.find_one({"github_user_id": 7, "kind": "website"})
    assert website["normalized_value"] == "https://arepasapp.com"


@pytest.mark.asyncio
async def test_null_public_email_stays_empty_when_nothing_is_published(db) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/ada":
            return httpx.Response(
                200,
                json=_profile(email=None, location="London", bio="Ada Lovelace writes software."),
            )
        if path == "/users/ada/repos":
            return httpx.Response(200, json=[])
        if path.endswith("/contents/README.md"):
            if request.url.params.get("ref") == "main":
                return httpx.Response(404, json={"message": "Not Found"})
            return _readme("I like Python. No address is listed.", "master")
        return httpx.Response(404, json={"message": "Not Found"})

    job_id, _candidate = await _seed(db, keywords=[])
    github = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    await EnrichmentService(db, github).run(job_id)
    await github.aclose()

    stored = await db.candidates.find_one({"github_user_id": 7})
    assert stored["public_email"] is None
    assert stored["country"] is None
    assert await db.contact_evidence.count_documents({"github_user_id": 7, "kind": "email"}) == 0
    website = await db.contact_evidence.find_one({"github_user_id": 7, "kind": "website"})
    assert website["normalized_value"] == "https://example.com"
    assert "ada@example.com" not in website["normalized_value"]


@pytest.mark.asyncio
async def test_profile_api_email_is_stored_and_noreply_is_ignored(db) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/ada":
            return httpx.Response(
                200,
                json=_profile(email="Ada@Example.com", location="Berlin, Germany"),
            )
        if request.url.path.endswith("/contents/README.md"):
            return _readme("Also ada@users.noreply.github.com", "main")
        return httpx.Response(200, json=[])

    job_id, _candidate = await _seed(db, keywords=[])
    github = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    await EnrichmentService(db, github).run(job_id)
    await github.aclose()

    stored = await db.candidates.find_one({"github_user_id": 7})
    assert stored["public_email"] == "Ada@example.com"
    assert stored["country"] == "Germany"
    assert stored["utc_offset"] is None
    emails = await db.contact_evidence.find({"kind": "email"}).to_list(10)
    assert [item["normalized_value"] for item in emails] == ["Ada@example.com"]


def test_utc_offset_formats_half_hours() -> None:
    assert format_utc_offset(-3.0) == "-03:00"
    assert format_utc_offset(5.5) == "+05:30"
    assert format_utc_offset(0) == "+00:00"
    assert format_utc_offset(14) == "+14:00"
    assert format_utc_offset(20) is None
    html = '<profile-timezone data-hours-ahead-of-utc="-3.0">(UTC -03:00)</profile-timezone>'
    assert parse_utc_offset(html) == "-03:00"
    assert parse_utc_offset('<profile-timezone data-hours-ahead-of-utc="5.5">') == "+05:30"
    page = (
        '<a href="mailto:Ada@Example.com">mail</a>'
        '<span itemprop="email">other@example.com</span>'
        "<p>hidden@example.com</p>"
        '<a href="mailto:ada@users.noreply.github.com">noreply</a>'
    )
    assert parse_profile_emails(page) == ["Ada@example.com", "other@example.com"]


@pytest.mark.asyncio
async def test_profile_html_stores_offset_and_keeps_api_email_primary(db) -> None:
    html = """
    <profile-timezone data-hours-ahead-of-utc="5.5"></profile-timezone>
    <a href="mailto:other@example.com">other</a>
    <span itemprop="email">third@example.com</span>
    <p>not-published@example.com</p>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert ".patch" not in path
        assert "/commits" not in path
        if request.headers.get("accept") == "text/html":
            return httpx.Response(200, text=html)
        if path == "/users/ada":
            return httpx.Response(
                200,
                json=_profile(email="Ada@Example.com", location="Brazil"),
            )
        return httpx.Response(404, json={"message": "Not Found"})

    job_id, _candidate = await _seed(db, keywords=[])
    github = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    outcome = await EnrichmentService(db, github).run(job_id)
    await github.aclose()

    assert outcome == "done"
    stored = await db.candidates.find_one({"github_user_id": 7})
    assert stored["name"] == "Ada Lovelace"
    assert stored["country"] == "Brazil"
    assert stored["utc_offset"] == "+05:30"
    assert stored["public_email"] == "Ada@example.com"
    evidence = await db.contact_evidence.find({"github_user_id": 7, "kind": "email"}).to_list(10)
    assert {item["normalized_value"] for item in evidence} == {
        "Ada@example.com",
        "other@example.com",
        "third@example.com",
    }
    assert all(item["source_url"] == "https://github.com/ada" for item in evidence)
    assert "not-published@example.com" not in {item["normalized_value"] for item in evidence}
    row = build_row(stored, evidence)
    assert row["public_email"] == "Ada@example.com"
    assert row["utc_offset"] == "+05:30"


@pytest.mark.asyncio
async def test_recent_profile_copies_utc_offset(db) -> None:
    job_id, _candidate = await _seed(db, keywords=[])
    other = new_job_document(SearchCriteria(countries=["Germany"]))
    await db.search_jobs.insert_one(other)
    await db.candidates.insert_one(
        {
            "job_id": other["_id"],
            "github_user_id": 7,
            "login": "ada",
            "profile_url": "https://github.com/ada",
            "enrichment_status": "enriched",
            "collection_status": "enriched",
            "last_checked_at": utcnow(),
            "name": "Ada Lovelace",
            "public_email": "ada@example.com",
            "country": "Brazil",
            "utc_offset": "-03:00",
            "location_raw": "Brazil",
        }
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("a fresh profile is copied without another fetch")

    github = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    outcome = await EnrichmentService(db, github).run(job_id)
    await github.aclose()

    assert outcome == "done"
    stored = await db.candidates.find_one({"job_id": job_id, "github_user_id": 7})
    assert stored["utc_offset"] == "-03:00"
    assert stored["name"] == "Ada Lovelace"
    assert stored["country"] == "Brazil"
    assert stored["public_email"] == "ada@example.com"


@pytest.mark.asyncio
async def test_bad_profile_url_does_not_stop_the_next_profile(db) -> None:
    job = new_job_document(SearchCriteria(countries=["Germany"], keywords=[]))
    job["status"] = "running"
    job["checkpoint"] = {"phase": "discovery_done", "windows": [], "last_enriched_user_id": None}
    await db.search_jobs.insert_one(job)
    await db.candidates.insert_many(
        [
            {
                "job_id": job["_id"],
                "github_user_id": 7,
                "login": "ada",
                "profile_url": "https://github.com/ada",
                "enrichment_status": "pending",
                "collection_status": "discovered",
                "public_email": None,
            },
            {
                "job_id": job["_id"],
                "github_user_id": 8,
                "login": "grace",
                "profile_url": "https://github.com/grace",
                "enrichment_status": "pending",
                "collection_status": "discovered",
                "public_email": None,
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("accept") == "text/html":
            return httpx.Response(200, text="<html></html>")
        if request.url.path == "/users/ada":
            return httpx.Response(
                200,
                json=_profile(
                    email="ada@example.com",
                    location="Berlin, Germany",
                    bio="Notes at http://[::1]/path",
                ),
            )
        if request.url.path == "/users/grace":
            profile = _profile(
                email="grace@example.com", location="Berlin, Germany", bio="Computers"
            )
            profile["login"] = "grace"
            profile["name"] = "Grace Hopper"
            return httpx.Response(200, json=profile)
        return httpx.Response(404, json={"message": "Not Found"})

    github = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    outcome = await EnrichmentService(db, github).run(job["_id"])
    await github.aclose()

    assert outcome == "done"
    ada = await db.candidates.find_one({"github_user_id": 7})
    grace = await db.candidates.find_one({"github_user_id": 8})
    assert ada["enrichment_status"] == "enriched"
    assert ada["public_email"] == "ada@example.com"
    assert ada["name"] == "Ada Lovelace"
    assert grace["enrichment_status"] == "enriched"
    assert grace["public_email"] == "grace@example.com"
    assert grace["name"] == "Grace Hopper"
    stored_job = await db.search_jobs.find_one({"_id": job["_id"]})
    assert stored_job["status"] == "running"
    assert stored_job["error"] is None
