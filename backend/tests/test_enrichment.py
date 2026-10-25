import base64

import httpx
import pytest

from app.discovery import new_job_document, upsert_candidate
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
    stored = await db.candidates.find_one({"job_id": job_id, "github_user_id": 7})
    assert stored["public_email"] == "ada@example.com"
    assert stored["country"] is None
    assert stored["location_confidence"] == 0.0
    assert "us_state" not in stored
    assert "primary_languages" not in stored
    assert "matched_keywords" not in stored
    assert "relevant_repository_urls" not in stored
    assert not any(path == "/users/ada/repos" for path, _ref in calls)
    email = await db.contact_evidence.find_one({"github_user_id": 7, "kind": "email"})
    assert email["normalized_value"] == "ada@example.com"
    assert email["source_url"].endswith("/README.md")
    website = await db.contact_evidence.find_one({"github_user_id": 7, "kind": "website"})
    assert website["normalized_value"] == "https://example.com"


def _repo(name: str, updated: str, *, fork: bool = False, archived: bool = False) -> dict:
    return {
        "name": name,
        "fork": fork,
        "archived": archived,
        "html_url": f"https://github.com/ada/{name}",
        "updated_at": updated,
    }


@pytest.mark.asyncio
async def test_repo_readmes_stop_when_an_email_is_found(db) -> None:
    """A profile with no email or LinkedIn reads recent READMEs until an address appears."""

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path == "/users/ada":
            return httpx.Response(
                200,
                json=_profile(email=None, location="Buenos Aires, Argentina", bio=None)
                | {"blog": "https://arepasapp.com", "company": None, "public_repos": 57},
            )
        if path == "/users/ada/repos":
            return httpx.Response(
                200,
                json=[
                    _repo("ada", "2025-08-01T00:00:00Z"),
                    _repo("newest", "2024-12-03T00:00:00Z"),
                    _repo("second", "2024-12-02T00:00:00Z"),
                    _repo("third", "2024-12-01T00:00:00Z"),
                    _repo("ancient", "2018-02-25T00:00:00Z"),
                    _repo("forked", "2025-01-01T00:00:00Z", fork=True),
                    _repo("archived-repo", "2025-06-01T00:00:00Z", archived=True),
                ],
            )
        if path in {"/repos/ada/newest/readme", "/repos/ada/second/readme"}:
            return _readme("Utilities.", "HEAD")
        if path == "/repos/ada/third/readme":
            return _readme("To contact me write me at <jscarton@gmail.com>", "HEAD")
        if path in {
            "/repos/ada/ancient/readme",
            "/repos/ada/forked/readme",
            "/repos/ada/archived-repo/readme",
        }:
            raise AssertionError(path)
        return httpx.Response(404, json={"message": "Not Found"})

    job_id, _candidate = await _seed(db, keywords=[])
    github = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    outcome = await EnrichmentService(db, github).run(job_id)
    await github.aclose()

    assert outcome == "done"
    stored = await db.candidates.find_one({"job_id": job_id, "github_user_id": 7})
    assert stored["country"] == "Argentina"
    assert stored["public_email"] == "jscarton@gmail.com"
    assert stored["collection_status"] == "enriched"
    email = await db.contact_evidence.find_one({"github_user_id": 7, "kind": "email"})
    assert email["normalized_value"] == "jscarton@gmail.com"
    assert email["source_url"].endswith("/README.md")
    website = await db.contact_evidence.find_one({"github_user_id": 7, "kind": "website"})
    assert website["normalized_value"] == "https://arepasapp.com"
    assert "/repos/ada/newest/readme" in calls
    assert "/repos/ada/second/readme" in calls
    assert "/repos/ada/third/readme" in calls
    assert "/users/ada/repos" in calls


@pytest.mark.asyncio
async def test_repository_readmes_stop_after_three(db) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path == "/users/ada":
            return httpx.Response(200, json=_profile(email=None, location="Remote", bio=None))
        if path == "/users/ada/repos":
            return httpx.Response(
                200,
                json=[
                    _repo("one", "2024-04-01T00:00:00Z"),
                    _repo("two", "2024-03-01T00:00:00Z"),
                    _repo("three", "2024-02-01T00:00:00Z"),
                    _repo("fourth", "2024-01-01T00:00:00Z"),
                    _repo("forked", "2025-01-01T00:00:00Z", fork=True),
                ],
            )
        if path in {"/repos/ada/fourth/readme", "/repos/ada/forked/readme"}:
            raise AssertionError(path)
        if path.endswith("/readme"):
            return _readme("No address is listed.", "HEAD")
        return httpx.Response(404, json={"message": "Not Found"})

    job_id, _candidate = await _seed(db, keywords=[])
    github = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    outcome = await EnrichmentService(db, github).run(job_id)
    await github.aclose()

    assert outcome == "done"
    stored = await db.candidates.find_one({"github_user_id": 7})
    assert stored["public_email"] is None
    assert "/repos/ada/one/readme" in calls
    assert "/repos/ada/two/readme" in calls
    assert "/repos/ada/three/readme" in calls
    assert await db.contact_evidence.count_documents({"github_user_id": 7, "kind": "email"}) == 0


@pytest.mark.asyncio
async def test_profile_linkedin_skips_repository_readmes(db) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/ada":
            profile = _profile(email=None, location="Berlin, Germany", bio=None)
            profile["blog"] = "https://www.linkedin.com/in/ada"
            return httpx.Response(200, json=profile)
        if path == "/users/ada/repos":
            raise AssertionError(path)
        return httpx.Response(404, json={"message": "Not Found"})

    job_id, _candidate = await _seed(db, keywords=[])
    github = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    outcome = await EnrichmentService(db, github).run(job_id)
    await github.aclose()

    assert outcome == "done"
    stored = await db.candidates.find_one({"github_user_id": 7})
    assert stored["public_email"] is None
    linkedin = await db.contact_evidence.find_one({"github_user_id": 7, "kind": "linkedin"})
    assert linkedin["normalized_value"] == "https://www.linkedin.com/in/ada"
    assert await db.contact_evidence.count_documents({"github_user_id": 7, "kind": "email"}) == 0


@pytest.mark.asyncio
async def test_email_and_linkedin_skip_the_profile_readme(db) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), request.headers.get("accept", "")))
        path = request.url.path
        if request.headers.get("accept") == "text/html":
            return httpx.Response(
                200,
                text='<profile-timezone data-hours-ahead-of-utc="1"></profile-timezone>',
            )
        if path == "/users/ada":
            profile = _profile(email="Ada@Example.com", location="Berlin, Germany")
            profile["blog"] = "https://www.linkedin.com/in/ada"
            return httpx.Response(200, json=profile)
        if "readme" in path.casefold() or path == "/users/ada/repos":
            raise AssertionError(path)
        return httpx.Response(404, json={"message": "Not Found"})

    job_id, _candidate = await _seed(db, keywords=[])
    github = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    outcome = await EnrichmentService(db, github).run(job_id)
    await github.aclose()

    assert outcome == "done"
    assert any(url.endswith("/users/ada") for url, _accept in calls)
    assert any(accept == "text/html" for _url, accept in calls)
    stored = await db.candidates.find_one({"github_user_id": 7})
    assert stored["public_email"] == "Ada@example.com"
    assert stored["utc_offset"] == "+01:00"
    linkedin = await db.contact_evidence.find_one({"github_user_id": 7, "kind": "linkedin"})
    assert linkedin["normalized_value"] == "https://www.linkedin.com/in/ada"


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
    assert stored["has_email"] is True
    assert stored["personal"] is False
    assert stored["other"] is True
    assert stored["has_linkedin"] is False
    assert stored["linkedin_url"] is None
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
async def test_reenrich_appends_contacts_and_keeps_stored_linkedin(db) -> None:
    job_id, _candidate = await _seed(db, keywords=[])
    await db.contact_evidence.insert_many(
        [
            {
                "github_user_id": 7,
                "kind": "email",
                "normalized_value": "old@example.com",
                "raw_value": "old@example.com",
                "source_url": "https://github.com/ada",
                "validation_status": "valid",
            },
            {
                "github_user_id": 7,
                "kind": "linkedin",
                "normalized_value": "https://www.linkedin.com/in/old",
                "raw_value": "https://www.linkedin.com/in/old",
                "source_url": "https://github.com/ada",
                "validation_status": "valid",
            },
        ]
    )
    other = new_job_document(SearchCriteria(countries=["Germany"], keywords=[]))
    other["status"] = "running"
    other["checkpoint"] = {"phase": "discovery_done", "windows": [], "last_enriched_user_id": None}
    await db.search_jobs.insert_one(other)
    assert (
        await upsert_candidate(
            db, other["_id"], {"id": 7, "login": "ada", "html_url": "https://github.com/ada"}
        )
        == "already_stored"
    )
    other_job = await db.search_jobs.find_one({"_id": other["_id"]})
    assert other_job["counts"]["discovered"] == 0
    moved = await db.candidates.find_one({"login": "ada"})
    assert moved["job_id"] == other["_id"]
    assert moved["enrichment_status"] == "merge"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/ada":
            return httpx.Response(
                200,
                json=_profile(email="Ada@Example.com", location="Berlin, Germany"),
            )
        if request.url.path == "/users/ada/repos":
            raise AssertionError(request.url.path)
        return httpx.Response(404, json={"message": "Not Found"})

    github = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    outcome = await EnrichmentService(db, github).run(other["_id"])
    await github.aclose()

    assert outcome == "done"
    emails = await db.contact_evidence.find({"github_user_id": 7, "kind": "email"}).to_list(10)
    assert {item["normalized_value"] for item in emails} == {"old@example.com", "Ada@example.com"}
    linkedin = await db.contact_evidence.find_one({"github_user_id": 7, "kind": "linkedin"})
    assert linkedin["normalized_value"] == "https://www.linkedin.com/in/old"
    stored = await db.candidates.find_one({"login": "ada"})
    assert stored["job_id"] == other["_id"]
    assert stored["public_email"] == "Ada@example.com"
    assert await db.candidates.count_documents({"job_id": job_id}) == 0


@pytest.mark.asyncio
async def test_merge_fills_gaps_and_resets_status_only_when_the_email_changes(db) -> None:
    job = new_job_document(SearchCriteria(countries=["Germany"], keywords=[]))
    job["status"] = "running"
    job["checkpoint"] = {"phase": "discovery_done", "windows": [], "last_enriched_user_id": None}
    await db.search_jobs.insert_one(job)
    await db.candidates.insert_one(
        {
            "job_id": job["_id"],
            "github_user_id": 7,
            "login": "ada",
            "profile_url": "https://github.com/ada",
            "enrichment_status": "merge",
            "collection_status": "enriched",
            "name": "Ada Lovelace",
            "company": None,
            "public_email": "ada@example.com",
            "outreach_status": "interested",
        }
    )

    def handler(email: str):
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/users/ada":
                return httpx.Response(
                    200,
                    json=_profile(email=email, location="Berlin, Germany"),
                )
            return httpx.Response(404, json={"message": "Not Found"})

        return respond

    github = GitHubClient("test-token", transport=httpx.MockTransport(handler("ada@example.com")))
    assert await EnrichmentService(db, github).run(job["_id"]) == "done"
    await github.aclose()
    stored = await db.candidates.find_one({"login": "ada"})
    assert stored["name"] == "Ada Lovelace"
    assert stored["company"] == "Analytical Engines"
    assert stored["public_email"] == "ada@example.com"
    assert stored["has_email"] is True
    assert stored["other"] is True
    assert stored["outreach_status"] == "interested"
    assert stored["country"] == "Germany"

    await db.candidates.update_one({"login": "ada"}, {"$set": {"enrichment_status": "merge"}})
    await db.search_jobs.update_one(
        {"_id": job["_id"]},
        {"$set": {"checkpoint.last_enriched_user_id": None}},
    )
    github = GitHubClient("test-token", transport=httpx.MockTransport(handler("grace@example.com")))
    assert await EnrichmentService(db, github).run(job["_id"]) == "done"
    await github.aclose()
    changed = await db.candidates.find_one({"login": "ada"})
    assert changed["name"] == "Ada Lovelace"
    assert changed["company"] == "Analytical Engines"
    assert changed["public_email"] == "grace@example.com"
    assert changed["has_email"] is True
    assert changed["personal"] is False
    assert changed["other"] is True
    assert "outreach_status" not in changed


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


@pytest.mark.asyncio
async def test_dns_failure_stays_on_the_same_candidate(db) -> None:
    job = new_job_document(SearchCriteria(countries=["Germany"], keywords=[]))
    job["status"] = "running"
    job["checkpoint"] = {"phase": "discovery_done", "windows": [], "last_enriched_user_id": None}
    await db.search_jobs.insert_one(job)
    await db.candidates.insert_one(
        {
            "job_id": job["_id"],
            "github_user_id": 7,
            "login": "ada",
            "profile_url": "https://github.com/ada",
            "enrichment_status": "pending",
            "collection_status": "discovered",
            "public_email": None,
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            raise OSError(11001, "getaddrinfo failed")
        except OSError as exc:
            raise httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=request) from exc

    async def sleeper(_seconds: float) -> None:
        return None

    github = GitHubClient("test-token", transport=httpx.MockTransport(handler), sleeper=sleeper)
    with pytest.raises(RuntimeError, match="Could not resolve api.github.com"):
        await EnrichmentService(db, github).run(job["_id"])
    await github.aclose()

    stored = await db.candidates.find_one({"github_user_id": 7})
    stored_job = await db.search_jobs.find_one({"_id": job["_id"]})
    assert stored["enrichment_status"] == "pending"
    assert stored_job["counts"]["errors"] == 0
    assert stored_job["checkpoint"]["last_enriched_user_id"] is None
