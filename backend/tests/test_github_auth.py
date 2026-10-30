import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest

from app.config import Settings
from app.github import GitHubClient
from app.github_auth import GitHubAppAuth, GitHubAppConfigError, GitHubAuthError
from app.main import create_app
from conftest import make_rsa_key, private_pem

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
NO_TIME_CHECKS = {"verify_iat": False, "verify_exp": False}


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class Exchange:
    """Mock of POST /app/installations/{id}/access_tokens."""

    def __init__(self, clock: Clock, *, lifetime: timedelta = timedelta(hours=1)) -> None:
        self.clock = clock
        self.lifetime = lifetime
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        expires = self.clock.now + self.lifetime
        return httpx.Response(
            201,
            json={
                "token": f"ghs_token{len(self.requests)}",
                "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )


@pytest.fixture
def key_file(tmp_path: Path, rsa_key) -> Path:
    path = tmp_path / "app.pem"
    path.write_text(private_pem(rsa_key), encoding="utf-8")
    return path


def _auth(key_file: Path, handler, clock: Clock, **overrides) -> GitHubAppAuth:
    options = {
        "app_id": "12345",
        "installation_id": "67890",
        "private_key_path": str(key_file),
        "clock": clock,
        "transport": httpx.MockTransport(handler),
    }
    options.update(overrides)
    return GitHubAppAuth(**options)


def _app_jwt(request: httpx.Request) -> str:
    return request.headers["authorization"].split(" ", 1)[1]


@pytest.mark.asyncio
async def test_exchange_sends_a_signed_app_jwt(key_file: Path, rsa_key) -> None:
    clock = Clock()
    exchange = Exchange(clock)
    auth = _auth(key_file, exchange, clock)

    assert await auth.token() == "ghs_token1"
    await auth.aclose()

    request = exchange.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/app/installations/67890/access_tokens"
    assert request.headers["authorization"].startswith("Bearer ")
    app_jwt = _app_jwt(request)
    assert jwt.get_unverified_header(app_jwt)["alg"] == "RS256"
    claims = jwt.decode(app_jwt, rsa_key.public_key(), algorithms=["RS256"], options=NO_TIME_CHECKS)
    now = int(NOW.timestamp())
    assert claims["iss"] == "12345"
    assert claims["iat"] == now - 60
    assert 0 < claims["exp"] - now <= 10 * 60


@pytest.mark.asyncio
async def test_jwt_signature_fails_against_another_key(key_file: Path) -> None:
    clock = Clock()
    exchange = Exchange(clock)
    auth = _auth(key_file, exchange, clock)
    await auth.token()
    await auth.aclose()

    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            _app_jwt(exchange.requests[0]),
            make_rsa_key().public_key(),
            algorithms=["RS256"],
            options=NO_TIME_CHECKS,
        )


@pytest.mark.asyncio
async def test_cached_token_is_reused(key_file: Path) -> None:
    clock = Clock()
    exchange = Exchange(clock)
    auth = _auth(key_file, exchange, clock)

    first = await auth.token()
    clock.now += timedelta(minutes=30)
    second = await auth.token()
    await auth.aclose()

    assert first == second == "ghs_token1"
    assert len(exchange.requests) == 1


@pytest.mark.asyncio
async def test_token_refreshes_shortly_before_expiry(key_file: Path) -> None:
    clock = Clock()
    exchange = Exchange(clock)
    auth = _auth(key_file, exchange, clock)

    assert await auth.token() == "ghs_token1"
    clock.now += timedelta(minutes=54)
    assert await auth.token() == "ghs_token1"
    clock.now += timedelta(minutes=2)
    assert await auth.token() == "ghs_token2"
    await auth.aclose()

    assert len(exchange.requests) == 2


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_exchange(key_file: Path) -> None:
    calls = {"count": 0}

    async def handler(_request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        await asyncio.sleep(0.01)
        return httpx.Response(
            201, json={"token": "ghs_shared", "expires_at": "2030-01-01T13:00:00Z"}
        )

    auth = _auth(key_file, handler, Clock())
    tokens = await asyncio.gather(*(auth.token() for _ in range(10)))
    await auth.aclose()

    assert tokens == ["ghs_shared"] * 10
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_invalidate_only_drops_the_matching_token(key_file: Path) -> None:
    clock = Clock()
    exchange = Exchange(clock)
    auth = _auth(key_file, exchange, clock)

    stale = await auth.token()
    auth.invalidate("some-other-token")
    assert await auth.token() == stale
    auth.invalidate(stale)
    assert await auth.token() == "ghs_token2"
    await auth.aclose()


@pytest.mark.asyncio
async def test_key_is_read_each_time_a_jwt_is_signed(key_file: Path) -> None:
    clock = Clock()
    exchange = Exchange(clock, lifetime=timedelta(minutes=1))
    auth = _auth(key_file, exchange, clock)
    await auth.token()

    replacement = make_rsa_key()
    key_file.write_text(private_pem(replacement), encoding="utf-8")
    await auth.token()
    await auth.aclose()

    jwt.decode(
        _app_jwt(exchange.requests[1]),
        replacement.public_key(),
        algorithms=["RS256"],
        options=NO_TIME_CHECKS,
    )


@pytest.mark.asyncio
async def test_github_refusal_names_the_setting_without_leaking_secrets(key_file: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    auth = _auth(key_file, handler, Clock())
    with pytest.raises(GitHubAuthError, match="GITHUB_APP_INSTALLATION_ID") as caught:
        await auth.token()
    await auth.aclose()
    assert "404" in str(caught.value)
    assert "BEGIN" not in str(caught.value)


@pytest.mark.asyncio
async def test_unreadable_exchange_response_is_an_auth_error(key_file: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"unexpected": True})

    auth = _auth(key_file, handler, Clock())
    with pytest.raises(GitHubAuthError, match="cannot read"):
        await auth.token()
    await auth.aclose()


@pytest.mark.parametrize(
    ("overrides", "name"),
    [
        ({"app_id": ""}, "GITHUB_APP_ID"),
        ({"installation_id": " "}, "GITHUB_APP_INSTALLATION_ID"),
        ({"private_key_path": ""}, "GITHUB_APP_PRIVATE_KEY_PATH"),
    ],
)
def test_missing_setting_is_a_config_error(key_file: Path, overrides: dict, name: str) -> None:
    with pytest.raises(GitHubAppConfigError, match=name):
        _auth(key_file, Exchange(Clock()), Clock(), **overrides)


def test_all_missing_settings_are_listed() -> None:
    with pytest.raises(GitHubAppConfigError) as caught:
        GitHubAppAuth(app_id="", installation_id="", private_key_path="")
    for name in ("GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID", "GITHUB_APP_PRIVATE_KEY_PATH"):
        assert name in str(caught.value)


def test_unreadable_key_file_is_a_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pem"
    with pytest.raises(GitHubAppConfigError, match="could not be read") as caught:
        _auth(missing, Exchange(Clock()), Clock())
    assert str(missing) in str(caught.value)


def test_directory_instead_of_key_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(GitHubAppConfigError, match="could not be read"):
        _auth(tmp_path, Exchange(Clock()), Clock())


def test_invalid_key_contents_are_a_config_error_that_does_not_echo_them(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pem"
    bad.write_text("this is not a key, super-secret-marker", encoding="utf-8")
    with pytest.raises(GitHubAppConfigError, match="not a valid RSA private key") as caught:
        _auth(bad, Exchange(Clock()), Clock())
    assert "super-secret-marker" not in str(caught.value)


@pytest.mark.asyncio
async def test_client_sends_the_installation_token_as_bearer(key_file: Path) -> None:
    clock = Clock()
    exchange = Exchange(clock)
    api: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return exchange(request)
        api.append(request.headers["authorization"])
        return httpx.Response(200, json={"login": "octocat"})

    auth = _auth(key_file, handler, clock)
    client = GitHubClient(auth, transport=httpx.MockTransport(handler))
    await client.get("/users/octocat")
    await client.get("/users/hubot")
    await client.aclose()

    assert api == ["Bearer ghs_token1", "Bearer ghs_token1"]
    assert len(exchange.requests) == 1


@pytest.mark.asyncio
async def test_api_refuses_to_start_without_app_settings(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "")
    app = create_app(settings=Settings(database_name="fetchiq_test"))
    with pytest.raises(GitHubAppConfigError, match="GITHUB_APP_ID"):
        async with app.router.lifespan_context(app):
            pass


def test_relative_key_path_resolves_from_the_repo_root() -> None:
    settings = Settings(github_app_private_key_path="keys/app.pem")
    path = Path(settings.github_app_private_key_path)
    assert path.is_absolute()
    assert path.parts[-2:] == ("keys", "app.pem")
    assert Settings(github_app_private_key_path="").github_app_private_key_path == ""
