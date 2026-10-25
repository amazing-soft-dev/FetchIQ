import socket
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import ConfigurationError, ServerSelectionTimeoutError

from app.config import FORBIDDEN_DATABASES, Settings
from app.contacts import SEED_STATUSES


def utcnow() -> datetime:
    return datetime.now(UTC)


def isoformat(value: object) -> str | None:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return None


def assert_database_allowed(name: str) -> None:
    if name in FORBIDDEN_DATABASES:
        raise RuntimeError(f"Refusing to open database {name}")


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create FetchIQ collections and the dedup indexes. Safe to call more than once."""

    assert_database_allowed(db.name)
    await db.search_presets.create_index([("name", 1)], name="preset_name")
    await db.search_jobs.create_index(
        [("status", 1), ("created_at", -1)],
        name="job_status_created",
    )
    candidate_indexes = await db.candidates.index_information()
    if "uniq_job_user" in candidate_indexes:
        await db.candidates.drop_index("uniq_job_user")
    await _collapse_duplicate_logins(db)
    await db.candidates.create_index([("login", 1)], unique=True, name="uniq_login")
    await db.candidates.create_index([("outreach_status", 1)], name="candidate_outreach_status")
    await db.candidates.create_index([("public_email", 1)], name="candidate_public_email")
    await db.candidates.create_index(
        [("last_checked_at", -1), ("login", 1)],
        name="candidate_checked_login",
    )
    await ensure_outreach_statuses(db)
    await db.contact_evidence.create_index(
        [("github_user_id", 1), ("kind", 1), ("normalized_value", 1)],
        unique=True,
        name="uniq_evidence",
    )
    await db.job_events.create_index(
        [("job_id", 1), ("created_at", 1)],
        name="job_events_time",
    )
    await db.campaigns.create_index(
        [("status", 1), ("created_at", -1)],
        name="campaign_status_created",
    )
    await db.campaign_recipients.create_index(
        [("campaign_id", 1), ("status", 1), ("position", 1)],
        name="recipient_campaign_status",
    )
    await db.campaign_recipients.create_index(
        [("campaign_id", 1), ("sent_at", 1)],
        name="recipient_campaign_sent",
    )


async def ensure_outreach_statuses(db: AsyncIOMotorDatabase) -> None:
    """Seed the status catalog once. Later edits stay as the user left them."""

    await db.outreach_statuses.create_index([("label", 1)], unique=True, name="uniq_status_label")
    if await db.outreach_statuses.count_documents({}) > 0:
        return
    await db.outreach_statuses.insert_many(
        [
            {"_id": status_id, "label": label, "position": position}
            for status_id, label, position in SEED_STATUSES
        ]
    )


async def _collapse_duplicate_logins(db: AsyncIOMotorDatabase) -> int:
    """Keep the newest candidate for each username so a unique login index can build."""

    best: dict[str, tuple[datetime, object]] = {}
    drop: list[object] = []
    cursor = db.candidates.find(
        {},
        {"login": 1, "updated_at": 1, "last_checked_at": 1, "discovered_at": 1},
    )
    async for document in cursor:
        login = document.get("login")
        if not isinstance(login, str) or not login:
            continue
        stamp = (
            document.get("updated_at")
            or document.get("last_checked_at")
            or document.get("discovered_at")
            or datetime.min.replace(tzinfo=UTC)
        )
        if getattr(stamp, "tzinfo", None) is None:
            stamp = stamp.replace(tzinfo=UTC)
        current = best.get(login)
        if current is None or stamp >= current[0]:
            if current is not None:
                drop.append(current[1])
            best[login] = (stamp, document["_id"])
        else:
            drop.append(document["_id"])
    if drop:
        await db.candidates.delete_many({"_id": {"$in": drop}})
    return len(drop)


def _mongodb_host(uri: str) -> str:
    """Hostname from a MongoDB URI. Usernames and passwords are left out."""

    netloc = urlsplit(uri).netloc
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    host = netloc.split(",", 1)[0].strip()
    if host.startswith("["):
        end = host.find("]")
        host = host[1:end] if end != -1 else ""
    elif ":" in host:
        host = host.split(":", 1)[0]
    host = host.strip().rstrip(".")
    if not host or any(mark in host for mark in ("@", "/", " ")):
        return "MongoDB"
    return host


def _is_dns_failure(exc: BaseException) -> bool:
    """True when a Mongo configuration error came from a hostname lookup."""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, socket.gaierror):
            return True
        if isinstance(current, OSError) and (
            current.errno == 11001 or getattr(current, "winerror", None) == 11001
        ):
            return True
        if type(current).__module__.startswith("dns."):
            return True
        text = str(current).casefold()
        if any(
            marker in text
            for marker in (
                "getaddrinfo",
                "name or service not known",
                "nodename nor servname",
                "temporary failure in name resolution",
                "dns query",
                "nameserver",
                "resolution lifetime",
            )
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _close_client(client: AsyncIOMotorClient | None) -> None:
    if client is not None:
        client.close()


async def connect(settings: Settings) -> tuple[AsyncIOMotorClient, AsyncIOMotorDatabase]:
    if not settings.mongodb_uri:
        raise RuntimeError("MONGODB_URI is required")
    assert_database_allowed(settings.database_name)
    client: AsyncIOMotorClient | None = None
    try:
        client = AsyncIOMotorClient(settings.mongodb_uri)
        database = client[settings.database_name]
        await ensure_indexes(database)
    except (ServerSelectionTimeoutError, ConfigurationError) as exc:
        _close_client(client)
        if isinstance(exc, ConfigurationError) and not _is_dns_failure(exc):
            raise
        raise RuntimeError(f"Could not resolve {_mongodb_host(settings.mongodb_uri)}") from None
    assert client is not None
    return client, database
