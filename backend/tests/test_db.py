from types import SimpleNamespace

import pytest
from pymongo.errors import ConfigurationError, ServerSelectionTimeoutError

from app.db import connect

_URI = "mongodb://reader:supersecret@db.internal.example:27017"


class _Client:
    def __init__(self, uri: str) -> None:
        self.uri = uri
        self.closed = False

    def __getitem__(self, name: str) -> dict:
        return {"name": name}

    def close(self) -> None:
        self.closed = True


def _settings() -> SimpleNamespace:
    return SimpleNamespace(mongodb_uri=_URI, database_name="fetchiq_test")


@pytest.mark.asyncio
async def test_connect_names_the_host_and_omits_credentials(monkeypatch) -> None:
    created: list[_Client] = []

    def client_factory(uri: str) -> _Client:
        client = _Client(uri)
        created.append(client)
        return client

    async def fail(_database) -> None:
        raise ServerSelectionTimeoutError(
            "db.internal.example:27017: [Errno 11001] getaddrinfo failed"
        )

    monkeypatch.setattr("app.db.AsyncIOMotorClient", client_factory)
    monkeypatch.setattr("app.db.ensure_indexes", fail)

    with pytest.raises(RuntimeError, match="Could not resolve db.internal.example") as caught:
        await connect(_settings())

    message = str(caught.value)
    assert "supersecret" not in message
    assert "reader" not in message
    assert "mongodb://" not in message
    assert created[0].closed


@pytest.mark.asyncio
async def test_connect_names_the_host_for_a_dns_configuration_error(monkeypatch) -> None:
    def client_factory(_uri: str) -> _Client:
        raise ConfigurationError(
            "The DNS query name does not exist: _mongodb._tcp.db.internal.example."
        )

    monkeypatch.setattr("app.db.AsyncIOMotorClient", client_factory)

    with pytest.raises(RuntimeError, match="Could not resolve db.internal.example") as caught:
        await connect(_settings())

    message = str(caught.value)
    assert "supersecret" not in message
    assert "reader" not in message


@pytest.mark.asyncio
async def test_connect_keeps_a_configuration_error_that_is_not_dns(monkeypatch) -> None:
    def client_factory(_uri: str) -> _Client:
        raise ConfigurationError("Collation is unsupported for unacknowledged writes.")

    monkeypatch.setattr("app.db.AsyncIOMotorClient", client_factory)

    with pytest.raises(ConfigurationError, match="Collation"):
        await connect(_settings())
