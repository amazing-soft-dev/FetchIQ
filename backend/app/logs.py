"""Process logging: one JSON object per line on stderr, configured once at startup.

Fields on every record: ``time``, ``level``, ``logger``, ``message``. Anything
passed through ``extra=`` is added as a top-level field, so call sites write
``log.info("job finished", extra={"job_id": job_id, "status": status})`` and
never format identifiers into the message. Secrets are never logged: callers
pass identifiers such as a job id or a login, not tokens, passwords, or
recipient addresses.
"""

import json
import logging
import sys
from datetime import UTC, datetime

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Attributes every LogRecord carries. Anything else on the record came from ``extra``.
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}

# Libraries that log every request at INFO. They stay at WARNING unless the app is at DEBUG.
_QUIET = ("httpx", "httpcore", "pymongo")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class JsonHandler(logging.StreamHandler):
    """Marker subclass so ``configure_logging`` can tell its own handler apart."""


def normalize_level(value: str) -> str:
    cleaned = value.strip().upper()
    if cleaned not in LEVELS:
        raise ValueError(f"unknown log level: {value}")
    return cleaned


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Attach the JSON handler to the root logger once and set the level.

    Safe to call more than once. Other handlers, such as pytest's capture
    handler, are left in place.
    """

    root = logging.getLogger()
    if not any(isinstance(handler, JsonHandler) for handler in root.handlers):
        handler = JsonHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(normalize_level(level))
    for name in _QUIET:
        logging.getLogger(name).setLevel(max(logging.WARNING, root.level))
    return root
