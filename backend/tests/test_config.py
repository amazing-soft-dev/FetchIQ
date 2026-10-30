import pytest

from app.config import Settings
from app.db import assert_database_allowed


def test_default_database_is_fetchiq() -> None:
    settings = Settings(mongodb_uri="", database_name="fetchiq")
    assert settings.database_name == "fetchiq"


@pytest.mark.parametrize("name", ["AIMS_local", "AthensDB", "admin", "config", "local"])
def test_settings_reject_foreign_databases(name: str) -> None:
    with pytest.raises(ValueError, match="not available"):
        Settings(database_name=name)


@pytest.mark.parametrize("name", ["AIMS_local", "AthensDB"])
def test_connect_guard_rejects_foreign_databases(name: str) -> None:
    with pytest.raises(RuntimeError, match="Refusing"):
        assert_database_allowed(name)
