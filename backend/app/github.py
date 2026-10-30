"""One GitHub App installation, Retry-After, and exponential backoff. No token pool."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.github_auth import TokenSource

RATE_LIMIT_STEP_SECONDS = 1.0
CONNECT_RETRY_DELAYS = (60, 120, 300)


class RateLimited(Exception):
    def __init__(
        self,
        *,
        retry_after: float,
        reset_at: datetime | None,
        limit: int | None,
        remaining: int | None,
    ) -> None:
        self.retry_after = retry_after
        self.reset_at = reset_at
        self.limit = limit
        self.remaining = remaining
        super().__init__("GitHub rate limit reached")


@dataclass(frozen=True)
class RateBudget:
    limit: int | None
    remaining: int | None
    reset_at: datetime | None

    def as_document(self, *, retry_after_seconds: float | None) -> dict:
        return {
            "limit": self.limit,
            "remaining": self.remaining,
            "reset_at": self.reset_at,
            "retry_after_seconds": retry_after_seconds,
        }


def parse_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        pieces = [piece.strip() for piece in part.split(";") if piece.strip()]
        if not pieces:
            continue
        url = pieces[0]
        if url.startswith("<") and url.endswith(">"):
            url = url[1:-1]
        rels = set(pieces[1:])
        if 'rel="next"' in rels or "rel=next" in rels:
            return url
    return None


def parse_last_page(link_header: str | None) -> int | None:
    """Page number of ``rel="last"`` in a Link header, or None when there is no such link."""

    if not link_header:
        return None
    for part in link_header.split(","):
        pieces = [piece.strip() for piece in part.split(";") if piece.strip()]
        if not pieces or not ('rel="last"' in pieces[1:] or "rel=last" in pieces[1:]):
            continue
        url = pieces[0].removeprefix("<").removesuffix(">")
        page = httpx.URL(url).params.get("page")
        if page and page.isdigit():
            return int(page)
    return None


def is_rate_limited(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    if response.headers.get("x-ratelimit-remaining") == "0":
        return True
    if response.headers.get("retry-after"):
        return True
    body = response.text.lower()
    return "rate limit" in body or "secondary rate" in body


def _optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _reset_at(response: httpx.Response) -> datetime | None:
    raw = response.headers.get("x-ratelimit-reset")
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("retry-after")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            try:
                when = parsedate_to_datetime(header)
            except (TypeError, ValueError, OSError):
                when = None
            if when is not None:
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                return max(0.0, (when - datetime.now(UTC)).total_seconds())
    reset_at = _reset_at(response)
    if reset_at is not None:
        return max(0.0, (reset_at - datetime.now(UTC)).total_seconds())
    return float(min(60, 2**attempt))


def read_budget(response: httpx.Response) -> RateBudget:
    return RateBudget(
        limit=_optional_int(response.headers.get("x-ratelimit-limit")),
        remaining=_optional_int(response.headers.get("x-ratelimit-remaining")),
        reset_at=_reset_at(response),
    )


def rate_limit_budget(exc: RateLimited, *, seconds_left: float) -> dict:
    """Budget stored while a job waits. Cleared once the wait finishes."""

    waiting = seconds_left > 0
    return {
        "limit": exc.limit,
        "remaining": exc.remaining,
        "reset_at": exc.reset_at if waiting else None,
        "retry_after_seconds": seconds_left if waiting else None,
    }


async def wait_out_rate_limit(seconds: float, sleeper, stopped) -> tuple[str | None, float]:
    """Sleep in short steps until the limit resets or the user stops the job.

    ``stopped`` is an async callable that returns ``"paused"`` or ``"cancelled"``
    when the user asked to stop, and ``None`` otherwise. The second value is the
    wait still remaining.
    """

    remaining = max(0.0, float(seconds))
    while True:
        reason = await stopped()
        if reason:
            return reason, remaining
        if remaining <= 0:
            return None, 0.0
        step = min(RATE_LIMIT_STEP_SECONDS, remaining)
        await sleeper(step)
        remaining = max(0.0, remaining - step)


class GitHubClient:
    """HTTP client that authenticates as the single GitHub App installation."""

    def __init__(
        self,
        auth: TokenSource,
        *,
        base_url: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
        sleeper=None,
    ) -> None:
        self._auth = auth
        self._sleeper = sleeper or asyncio.sleep
        self._client = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "FetchIQ",
            },
            timeout=httpx.Timeout(30.0),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._auth.aclose()

    def _is_api_url(self, url: str) -> bool:
        """True for relative paths and URLs on the API host. Other hosts get no credentials."""

        host = httpx.URL(url).host
        return not host or host == self._client.base_url.host

    def _request_host(self, url: str) -> str:
        """Hostname for this request, with no userinfo, path, or query."""

        host = httpx.URL(url).host
        if host:
            return host
        return self._client.base_url.host or "api.github.com"

    async def get(
        self,
        url: str,
        params: dict | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        send_auth = self._is_api_url(url)
        auth_retried = False
        connect_tries = 0
        server_tries = 0
        delay = 1.0
        while True:
            request_headers = dict(headers or {})
            token = None
            try:
                if send_auth:
                    token = await self._auth.token()
                    request_headers["Authorization"] = f"Bearer {token}"
                response = await self._client.get(url, params=params, headers=request_headers)
            except httpx.ConnectError:
                if connect_tries < len(CONNECT_RETRY_DELAYS):
                    await self._sleeper(CONNECT_RETRY_DELAYS[connect_tries])
                    connect_tries += 1
                    continue
                raise RuntimeError(f"Could not resolve {self._request_host(url)}") from None
            if response.status_code == 401 and token is not None and not auth_retried:
                # The installation token was revoked or expired early. Mint a new one once.
                auth_retried = True
                self._auth.invalidate(token)
                continue
            if is_rate_limited(response):
                raise RateLimited(
                    retry_after=retry_after_seconds(response, server_tries),
                    reset_at=_reset_at(response),
                    limit=_optional_int(response.headers.get("x-ratelimit-limit")),
                    remaining=_optional_int(response.headers.get("x-ratelimit-remaining")),
                )
            if response.status_code >= 500 and server_tries < 3:
                server_tries += 1
                await self._sleeper(delay)
                delay = min(delay * 2, 30.0)
                continue
            return response

    async def get_html(self, url: str) -> httpx.Response:
        """Fetch a public profile page. Overrides the API Accept header."""

        return await self.get(url, headers={"Accept": "text/html"})
