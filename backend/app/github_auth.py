"""GitHub App installation tokens: a short-lived JWT is exchanged for a one-hour token."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import httpx
import jwt

JWT_BACKDATE_SECONDS = 60
JWT_LIFETIME_SECONDS = 9 * 60
REFRESH_MARGIN = timedelta(minutes=5)


class GitHubAppConfigError(RuntimeError):
    """The GitHub App settings are missing or unusable."""


class GitHubAuthError(RuntimeError):
    """GitHub refused to issue an installation token."""


class TokenSource(Protocol):
    async def token(self) -> str: ...

    def invalidate(self, stale: str) -> None: ...

    async def aclose(self) -> None: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GitHubAppAuth:
    """Signs an app JWT, trades it for an installation token, and caches that token.

    The private key is read from disk each time a JWT is signed. Nothing here puts the key,
    the JWT, or the installation token in a message or a log line.
    """

    def __init__(
        self,
        *,
        app_id: str,
        installation_id: str,
        private_key_path: str,
        base_url: str = "https://api.github.com",
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        missing = [
            name
            for name, value in (
                ("GITHUB_APP_ID", app_id),
                ("GITHUB_APP_INSTALLATION_ID", installation_id),
                ("GITHUB_APP_PRIVATE_KEY_PATH", private_key_path),
            )
            if not value.strip()
        ]
        if missing:
            raise GitHubAppConfigError(
                "GitHub App authentication needs " + ", ".join(missing) + " in .env"
            )
        self._app_id = app_id.strip()
        self._installation_id = installation_id.strip()
        self._key_path = Path(private_key_path.strip()).expanduser()
        self._clock = clock
        self._token: str | None = None
        self._expires_at = datetime.min.replace(tzinfo=UTC)
        self._lock = asyncio.Lock()
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
        # Fail now, not on the first search, when the key file is absent or not an RSA key.
        self._signed_jwt()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _signed_jwt(self) -> str:
        try:
            key = self._key_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else "unreadable"
            raise GitHubAppConfigError(
                f"GITHUB_APP_PRIVATE_KEY_PATH {self._key_path} could not be read: {reason}"
            ) from None
        now = int(self._clock().timestamp())
        claims = {
            "iss": self._app_id,
            "iat": now - JWT_BACKDATE_SECONDS,
            "exp": now + JWT_LIFETIME_SECONDS,
        }
        try:
            return jwt.encode(claims, key, algorithm="RS256")
        except (jwt.PyJWTError, ValueError, TypeError):
            raise GitHubAppConfigError(
                f"GITHUB_APP_PRIVATE_KEY_PATH {self._key_path} is not a valid RSA private key"
            ) from None

    def _usable(self) -> bool:
        return self._token is not None and self._clock() < self._expires_at - REFRESH_MARGIN

    async def token(self) -> str:
        if self._token is not None and self._usable():
            return self._token
        async with self._lock:
            # Another task may have refreshed while this one waited for the lock.
            if self._token is not None and self._usable():
                return self._token
            self._token, self._expires_at = await self._exchange()
            return self._token

    def invalidate(self, stale: str) -> None:
        """Drop the cached token if it is still the one GitHub just rejected."""

        if self._token == stale:
            self._token = None

    async def _exchange(self) -> tuple[str, datetime]:
        response = await self._client.post(
            f"/app/installations/{self._installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {self._signed_jwt()}"},
        )
        if response.status_code != 201:
            raise GitHubAuthError(self._refusal(response))
        try:
            body = response.json()
            token = body["token"]
            expires_at = datetime.fromisoformat(body["expires_at"])
        except (ValueError, KeyError, TypeError):
            raise GitHubAuthError(
                "GitHub returned an installation token response we cannot read"
            ) from None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return token, expires_at

    def _refusal(self, response: httpx.Response) -> str:
        try:
            detail = str(response.json().get("message", ""))[:200]
        except (ValueError, AttributeError):
            detail = ""
        hint = ""
        if response.status_code == 401:
            hint = " Check GITHUB_APP_ID and that the private key belongs to this app."
        elif response.status_code == 404:
            hint = " Check GITHUB_APP_INSTALLATION_ID and that the app is still installed."
        suffix = f": {detail}" if detail else ""
        return (
            f"GitHub refused the installation token request (HTTP {response.status_code})"
            f"{suffix}.{hint}"
        )
