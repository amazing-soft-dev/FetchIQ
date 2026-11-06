from app.database import Client, Collection, Cursor, Database


async def test_the_driver_satisfies_the_protocols(mongo_client, db) -> None:
    assert isinstance(mongo_client, Client)
    assert isinstance(db, Database)
    assert isinstance(db.candidates, Collection)
    assert isinstance(db.candidates.find({}), Cursor)


async def test_each_test_gets_its_own_database_that_is_dropped(
    fresh_database, mongo_client
) -> None:
    first = fresh_database()
    second = fresh_database()
    assert first.name != second.name
    assert first.name.startswith("fetchiq_test_")
    await first.marker.insert_one({"_id": 1})
    assert await second.marker.count_documents({}) == 0
    assert first.name in await mongo_client.list_database_names()
