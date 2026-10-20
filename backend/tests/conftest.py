import pytest
from mongomock_motor import AsyncMongoMockClient

from app.db import ensure_indexes


@pytest.fixture
async def db():
    client = AsyncMongoMockClient()
    database = client["fetchiq_test"]
    await ensure_indexes(database)
    yield database
    client.close()
