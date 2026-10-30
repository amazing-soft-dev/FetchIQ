import httpx
import pytest

from app.commits import find_commit_email, patch_author_email
from app.discovery import new_job_document
from app.enrichment import EnrichmentService
from app.github import GitHubClient, parse_last_page
from app.models import SearchCriteria
from conftest import FakeAuth

SHA = "b71b2a585b7465e76b0748e85ebe00e31479211c"


def _patch(author: str, body: str = "") -> str:
    return (
        f"From {SHA} Mon Sep 17 00:00:00 2001\n"
        f"From: {author}\n"
        "Date: Thu, 24 Apr 2025 08:19:23 -0500\n"
        "Subject: [PATCH] Initial commit\n"
        "\n"
        "---\n"
        " README.md | 1 +\n"
        "\n"
        "diff --git a/README.md b/README.md\n"
        f"+{body}\n"
    )


def test_patch_author_email_reads_only_the_from_header() -> None:
    patch = _patch("amazing-soft-dev <tomasnrrs@gmail.com>", body="Contact diff@example.com")
    assert patch_author_email(patch) == "tomasnrrs@gmail.com"


def test_patch_author_email_skips_noreply_and_never_reads_the_diff() -> None:
    noreply = _patch(
        "amazing-soft-dev <208959585+amazing-soft-dev@users.noreply.github.com>",
        body="Contact diff@example.com",
    )
    assert patch_author_email(noreply) is None
    assert patch_author_email(_patch("Ada <ada@noreply.github.com>")) is None


def test_patch_author_email_unfolds_a_long_from_header() -> None:
    patch = (
        f"From {SHA} Mon Sep 17 00:00:00 2001\n"
        "From: =?UTF-8?q?Ada_Lovelace?=\n"
        " <Ada@Example.com>\n"
        "\n"
    )
    assert patch_author_email(patch) == "Ada@example.com"


def test_parse_last_page() -> None:
    link = (
        '<https://api.github.com/repositories/1/commits?per_page=1&page=2>; rel="next", '
        '<https://api.github.com/repositories/1/commits?per_page=1&page=7>; rel="last"'
    )
    assert parse_last_page(link) == 7
    assert parse_last_page('<https://api.github.com/x?page=2>; rel="next"') is None
    assert parse_last_page(None) is None


def _repo(name: str, created: str, *, fork: bool = False) -> dict:
    return {"name": name, "fork": fork, "created_at": created}


def _commit(repo: str, sha: str) -> dict:
    return {"sha": sha, "html_url": f"https://github.com/ada/{repo}/commit/{sha}"}


@pytest.mark.asyncio
async def test_first_commits_are_read_oldest_first_across_pages() -> None:
    """Seven commits, newest first: page 3 holds the oldest one, page 2 the next three."""

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params
        seen.append(f"{path}?{params.get('per_page')}&{params.get('page')}")
        if path == "/users/ada/repos":
            assert params["sort"] == "created" and params["direction"] == "asc"
            return httpx.Response(200, json=[_repo("alpha", "2020-01-01T00:00:00Z")])
        if path == "/repos/ada/alpha/commits":
            if params["per_page"] == "1":
                link = '<https://api.github.com/x?per_page=1&page=7>; rel="last"'
                return httpx.Response(200, headers={"Link": link}, json=[_commit("alpha", "c7")])
            pages = {
                "3": [_commit("alpha", "c1")],
                "2": [_commit("alpha", f"c{n}") for n in (4, 3, 2)],
            }
            return httpx.Response(200, json=pages[params["page"]])
        if path.endswith(".patch"):
            return httpx.Response(200, text=_patch("Ada <noreply@noreply.github.com>"))
        return httpx.Response(404, json={"message": "Not Found"})

    github = GitHubClient(FakeAuth(), transport=httpx.MockTransport(handler))
    assert await find_commit_email(github, "ada") is None
    await github.aclose()
    patches = [item.split("?")[0] for item in seen if ".patch" in item]
    assert patches == [
        "/ada/alpha/commit/c1.patch",
        "/ada/alpha/commit/c2.patch",
        "/ada/alpha/commit/c3.patch",
    ]


async def _seed(db) -> str:
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
    return job["_id"]


def _profile() -> dict:
    return {"id": 7, "login": "ada", "name": "Ada", "email": None, "public_repos": 5}


@pytest.mark.asyncio
async def test_enrichment_falls_back_to_commit_patches_and_stops_at_the_first_email(db) -> None:
    """The oldest repo is read first, a noreply commit is skipped, and later repos are not read.

    The fork and the later repositories are never asked for commits.
    """

    patch_urls: list[str] = []
    repos = [
        _repo("zeta", "2024-01-01T00:00:00Z"),
        _repo("forked", "2019-01-01T00:00:00Z", fork=True),
        _repo("beta", "2021-01-01T00:00:00Z"),
        _repo("alpha", "2020-01-01T00:00:00Z"),
        _repo("gamma", "2022-01-01T00:00:00Z"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params
        if path == "/users/ada":
            return httpx.Response(200, json=_profile())
        if path == "/users/ada/repos":
            return httpx.Response(200, json=repos)
        if path == "/repos/ada/alpha/commits":
            if params["per_page"] == "1":
                link = '<https://api.github.com/x?per_page=1&page=2>; rel="last"'
                return httpx.Response(200, headers={"Link": link}, json=[_commit("alpha", "a2")])
            return httpx.Response(200, json=[_commit("alpha", "a2"), _commit("alpha", "a1")])
        if path.endswith(".patch"):
            patch_urls.append(str(request.url))
            if path.endswith("/a1.patch"):
                return httpx.Response(
                    200, text=_patch("Ada <208959585+ada@users.noreply.github.com>")
                )
            return httpx.Response(200, text=_patch("Ada <ada@gmail.com>"))
        if "/commits" in path:
            raise AssertionError(path)
        return httpx.Response(404, json={"message": "Not Found"})

    job_id = await _seed(db)
    github = GitHubClient(FakeAuth(), transport=httpx.MockTransport(handler))
    assert await EnrichmentService(db, github).run(job_id) == "done"
    await github.aclose()

    assert patch_urls == [
        "https://github.com/ada/alpha/commit/a1.patch",
        "https://github.com/ada/alpha/commit/a2.patch",
    ]
    stored = await db.candidates.find_one({"login": "ada"})
    assert stored["public_email"] == "ada@gmail.com"
    evidence = await db.contact_evidence.find({"github_user_id": 7, "kind": "email"}).to_list(10)
    assert [item["normalized_value"] for item in evidence] == ["ada@gmail.com"]
    assert evidence[0]["source_url"] == "https://github.com/ada/alpha/commit/a2"


@pytest.mark.asyncio
async def test_only_the_three_oldest_owned_repositories_are_read() -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/ada/repos":
            return httpx.Response(
                200,
                json=[
                    _repo("d", "2023-01-01T00:00:00Z"),
                    _repo("b", "2021-01-01T00:00:00Z"),
                    _repo("fork", "2019-01-01T00:00:00Z", fork=True),
                    _repo("a", "2020-01-01T00:00:00Z"),
                    _repo("c", "2022-01-01T00:00:00Z"),
                ],
            )
        asked.append(path)
        return httpx.Response(409, json={"message": "Git Repository is empty."})

    github = GitHubClient(FakeAuth(), transport=httpx.MockTransport(handler))
    assert await find_commit_email(github, "ada") is None
    await github.aclose()
    assert asked == [f"/repos/ada/{name}/commits" for name in ("a", "b", "c")]


@pytest.mark.asyncio
async def test_commit_noreply_addresses_are_not_saved(db) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/ada":
            return httpx.Response(200, json=_profile())
        if path == "/users/ada/repos":
            return httpx.Response(200, json=[_repo("alpha", "2020-01-01T00:00:00Z")])
        if path == "/repos/ada/alpha/commits":
            return httpx.Response(200, json=[_commit("alpha", "a1")])
        if path.endswith(".patch"):
            return httpx.Response(
                200, text=_patch("Ada <208959585+ada@users.noreply.github.com>", body="x@y.com")
            )
        return httpx.Response(404, json={"message": "Not Found"})

    job_id = await _seed(db)
    github = GitHubClient(FakeAuth(), transport=httpx.MockTransport(handler))
    assert await EnrichmentService(db, github).run(job_id) == "done"
    await github.aclose()

    stored = await db.candidates.find_one({"login": "ada"})
    assert stored["public_email"] is None
    assert await db.contact_evidence.count_documents({"kind": "email"}) == 0
