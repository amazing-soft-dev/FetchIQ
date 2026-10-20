import base64

import httpx
import pytest

from app.discovery import new_job_document
from app.enrichment import EnrichmentService
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
        if "/forked/" in path or "/old/" in path or "/notes/" in path:
            raise AssertionError(path)
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
    emails = await db.contact_evidence.find({"kind": "email"}).to_list(10)
    assert [item["normalized_value"] for item in emails] == ["Ada@example.com"]
