import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from mongomock_motor import AsyncMongoMockClient

from app.db import ensure_indexes


def make_rsa_key() -> rsa.RSAPrivateKey:
    """A throwaway key for tests. Never a real GitHub App key."""

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def private_pem(key: rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture(scope="session")
def rsa_key() -> rsa.RSAPrivateKey:
    return make_rsa_key()


@pytest.fixture(autouse=True)
def github_app_env(monkeypatch, tmp_path_factory, rsa_key) -> None:
    """Settings() reads the environment, so apps built without a client get a fake app."""

    key_file = tmp_path_factory.mktemp("github-app") / "app.pem"
    key_file.write_text(private_pem(rsa_key), encoding="utf-8")
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", str(key_file))


@pytest.fixture
async def db():
    client = AsyncMongoMockClient()
    database = client["fetchiq_test"]
    await ensure_indexes(database)
    yield database
    client.close()


class FakeAuth:
    """Stands in for GitHubAppAuth so GitHub client tests need no key or token exchange."""

    def __init__(self, token: str = "test-token") -> None:
        self._token = token

    async def token(self) -> str:
        return self._token

    def invalidate(self, stale: str) -> None:
        return None

    async def aclose(self) -> None:
        return None
