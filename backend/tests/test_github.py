from datetime import UTC, datetime

import httpx
import pytest

from app.github import GitHubClient, RateLimited, parse_next_link


def test_parse_next_link() -> None:
    header = (
        '<https://api.github.com/search/users?page=2>; rel="next", '
        '<https://api.github.com/search/users?page=5>; rel="last"'
    )
    assert parse_next_link(header) == "https://api.github.com/search/users?page=2"
    assert parse_next_link(None) is None
    assert parse_next_link("") is None


@pytest.mark.asyncio
async def test_rate_limit_headers_pause_without_retrying() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(
            429,
            headers={
                "Retry-After": "12",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Limit": "30",
                "X-RateLimit-Reset": "1700000000",
            },
            json={"message": "rate limit exceeded"},
        )

    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    client = GitHubClient("test-token", transport=httpx.MockTransport(handler), sleeper=sleeper)
    with pytest.raises(RateLimited) as caught:
        await client.get("/search/users")
    assert calls["count"] == 1
    assert sleeps == []
    assert caught.value.retry_after == 12
    assert caught.value.remaining == 0
    assert caught.value.limit == 30
    assert caught.value.reset_at == datetime.fromtimestamp(1700000000, tz=UTC)
    await client.aclose()


@pytest.mark.asyncio
async def test_forbidden_rate_limit_uses_reset_header() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1700000123"},
            json={"message": "API rate limit exceeded"},
        )

    client = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    with pytest.raises(RateLimited) as caught:
        await client.get("/users/octocat")
    assert caught.value.reset_at == datetime.fromtimestamp(1700000123, tz=UTC)
    await client.aclose()


@pytest.mark.asyncio
async def test_ordinary_forbidden_is_returned() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Resource not accessible"})

    client = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    response = await client.get("/repos/octocat/missing")
    assert response.status_code == 403
    await client.aclose()


@pytest.mark.asyncio
async def test_server_errors_use_exponential_backoff() -> None:
    calls = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    client = GitHubClient("test-token", transport=httpx.MockTransport(handler), sleeper=sleeper)
    response = await client.get("/users/octocat")
    assert response.status_code == 200
    assert calls["count"] == 3
    assert sleeps == [1.0, 2.0]
    await client.aclose()


@pytest.mark.asyncio
async def test_html_fetch_overrides_accept_and_uses_the_profile_url() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers["accept"]))
        return httpx.Response(200, text="<html></html>")

    client = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    html = await client.get_html("https://github.com/octocat")
    api = await client.get("/users/octocat")
    await client.aclose()

    assert html.status_code == 200
    assert api.status_code == 200
    assert seen[0] == ("https://github.com/octocat", "text/html")
    assert seen[1][0].endswith("/users/octocat")
    assert seen[1][1] == "application/vnd.github+json"


@pytest.mark.asyncio
async def test_missing_token_does_not_call_github() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("request should not be sent")

    client = GitHubClient("", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        await client.get("/user")
    await client.aclose()


def _raise_dns_failure(request: httpx.Request) -> None:
    try:
        raise OSError(11001, "getaddrinfo failed")
    except OSError as exc:
        raise httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=request) from exc


@pytest.mark.asyncio
async def test_connect_error_retries_then_succeeds() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            _raise_dns_failure(request)
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    client = GitHubClient("test-token", transport=httpx.MockTransport(handler), sleeper=sleeper)
    response = await client.get("/users/octocat")
    assert response.status_code == 200
    assert calls["count"] == 2
    assert sleeps == [1.0]
    await client.aclose()


@pytest.mark.asyncio
async def test_connect_error_names_the_host() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        _raise_dns_failure(request)
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    client = GitHubClient("test-token", transport=httpx.MockTransport(handler), sleeper=sleeper)
    with pytest.raises(RuntimeError, match="Could not resolve api.github.com") as caught:
        await client.get("/users/octocat")
    assert "test-token" not in str(caught.value)
    assert calls["count"] == 4
    assert sleeps == [1.0, 2.0, 4.0]
    await client.aclose()
