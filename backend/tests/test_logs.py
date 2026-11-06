import json
import logging

import pytest

from app.campaigns import CampaignRunner
from app.config import Settings
from app.logs import JsonFormatter, JsonHandler, configure_logging, normalize_level


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, message, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_emits_one_json_object_with_extras() -> None:
    line = JsonFormatter().format(_record("job finished", job_id="j1", status="succeeded"))
    payload = json.loads(line)
    assert payload["message"] == "job finished"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["job_id"] == "j1"
    assert payload["status"] == "succeeded"
    assert payload["time"].endswith("+00:00")
    assert "args" not in payload
    assert "exc_info" not in payload


def test_formatter_includes_the_traceback() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.LogRecord(
            "app.test", logging.ERROR, __file__, 1, "job failed", (), __import__("sys").exc_info()
        )
    payload = json.loads(JsonFormatter().format(record))
    assert "RuntimeError: boom" in payload["exception"]


def test_formatter_serializes_values_json_cannot() -> None:
    from datetime import UTC, datetime

    stamp = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    payload = json.loads(JsonFormatter().format(_record("rate limited", reset_at=stamp)))
    assert payload["reset_at"] == "2024-01-02 03:04:05+00:00"


def test_configure_logging_adds_one_handler_and_keeps_others() -> None:
    root = logging.getLogger()
    before = [handler for handler in root.handlers if not isinstance(handler, JsonHandler)]
    configure_logging("debug")
    configure_logging("INFO")
    json_handlers = [handler for handler in root.handlers if isinstance(handler, JsonHandler)]
    assert len(json_handlers) == 1
    assert all(handler in root.handlers for handler in before)
    assert root.level == logging.INFO
    assert logging.getLogger("httpx").level == logging.WARNING


@pytest.mark.parametrize("value", ["debug", " Info ", "WARNING"])
def test_normalize_level_accepts_any_case(value: str) -> None:
    assert normalize_level(value) == value.strip().upper()


def test_normalize_level_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="unknown log level"):
        normalize_level("loud")


def test_settings_log_level_is_normalized() -> None:
    assert Settings(log_level="warning").log_level == "WARNING"
    with pytest.raises(ValueError, match="unknown log level"):
        Settings(log_level="loud")


async def test_campaign_finish_logs_the_status(db, caplog) -> None:
    runner = CampaignRunner(db, mailer=None)
    with caplog.at_level(logging.INFO, logger="app.campaigns"):
        await runner._finish("c1", "succeeded", None)
    record = next(item for item in caplog.records if item.getMessage() == "campaign finished")
    assert record.campaign_id == "c1"
    assert record.status == "succeeded"
