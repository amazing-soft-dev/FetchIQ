from mongomock_motor import AsyncMongoMockClient

from app.database import Client, Collection, Cursor, Database


def test_the_test_driver_satisfies_the_protocols() -> None:
    client = AsyncMongoMockClient()
    database = client["fetchiq_test"]
    assert isinstance(client, Client)
    assert isinstance(database, Database)
    assert isinstance(database.candidates, Collection)
    assert isinstance(database.candidates.find({}), Cursor)


async def test_the_production_driver_satisfies_the_protocols() -> None:
    from pymongo import AsyncMongoClient

    client = AsyncMongoClient("mongodb://localhost:27017", connect=False)
    try:
        database = client["fetchiq_test"]
        assert isinstance(client, Client)
        assert isinstance(database, Database)
        assert isinstance(database.candidates, Collection)
        assert isinstance(database.candidates.find({}), Cursor)
    finally:
        await client.close()
