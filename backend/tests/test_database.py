from mongomock_motor import AsyncMongoMockClient

from app.database import Client, Collection, Cursor, Database


def test_the_test_driver_satisfies_the_protocols() -> None:
    client = AsyncMongoMockClient()
    database = client["fetchiq_test"]
    assert isinstance(client, Client)
    assert isinstance(database, Database)
    assert isinstance(database.candidates, Collection)
    assert isinstance(database.candidates.find({}), Cursor)


def test_the_production_driver_satisfies_the_protocols() -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient("mongodb://localhost:27017", connect=False)
    try:
        database = client["fetchiq_test"]
        assert isinstance(client, Client)
        assert isinstance(database, Database)
        assert isinstance(database.candidates, Collection)
        assert isinstance(database.candidates.find({}), Cursor)
    finally:
        client.close()
