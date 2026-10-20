"""One GitHub token, Retry-After, and exponential backoff. No token pool."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx


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


class GitHubClient:
    """HTTP client bound to the single token from the environment."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
        sleeper=None,
    ) -> None:
        self._token = token
        self._sleeper = sleeper or asyncio.sleep
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "FetchIQ",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            headers=headers,
            timeout=httpx.Timeout(30.0),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, url: str, params: dict | None = None) -> httpx.Response:
        if not self._token:
            raise RuntimeError("GITHUB_TOKEN is required")
        attempt = 0
        delay = 1.0
        while True:
            response = await self._client.get(url, params=params)
            if is_rate_limited(response):
                raise RateLimited(
                    retry_after=retry_after_seconds(response, attempt),
                    reset_at=_reset_at(response),
                    limit=_optional_int(response.headers.get("x-ratelimit-limit")),
                    remaining=_optional_int(response.headers.get("x-ratelimit-remaining")),
                )
            if response.status_code >= 500 and attempt < 3:
                attempt += 1
                await self._sleeper(delay)
                delay = min(delay * 2, 30.0)
                continue
            return response
