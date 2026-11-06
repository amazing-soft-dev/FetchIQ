import os
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pymongo import AsyncMongoClient

from app.db import ensure_indexes

# A real server: `docker compose up mongo` from the repository root, or any local
# MongoDB. Every test gets its own fetchiq_test_<id> database and drops it after.
TEST_MONGODB_URI = os.environ.get("MONGODB_URI") or "mongodb://localhost:27017"


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
async def mongo_client():
    client = AsyncMongoClient(TEST_MONGODB_URI, tz_aware=True, serverSelectionTimeoutMS=5000)
    try:
        await client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001 - any driver error means the same thing here
        await client.close()
        pytest.fail(
            f"The backend tests need a MongoDB at {TEST_MONGODB_URI} "
            f"(set MONGODB_URI, or run `docker compose up mongo`): {exc}"
        )
    yield client
    await client.close()


@pytest.fixture
async def fresh_database(mongo_client):
    """A factory for empty databases, each dropped when the test ends."""

    names: list[str] = []

    def make():
        name = f"fetchiq_test_{uuid4().hex[:12]}"
        names.append(name)
        return mongo_client[name]

    yield make
    for name in names:
        await mongo_client.drop_database(name)


@pytest.fixture
async def db(fresh_database):
    database = fresh_database()
    await ensure_indexes(database)
    return database


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
