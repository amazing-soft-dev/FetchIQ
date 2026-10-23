import asyncio
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.campaigns import INTERRUPTED, CampaignRunner
from app.config import Settings
from app.mail import MailFailure
from app.main import create_app


def _candidate(login: str, email: str | None, **extra) -> dict:
    document = {
        "job_id": "job-1",
        "github_user_id": extra.pop("github_user_id", 7),
        "login": login,
        "profile_url": f"https://github.com/{login}",
        "name": extra.pop("name", login.title()),
        "public_email": email,
        "last_checked_at": datetime(2024, 1, 1, tzinfo=UTC),
    }
    document.update(extra)
    return document


class FakeMailer:
    def __init__(
        self,
        refuse: set[str] | None = None,
        stop: bool = False,
        copy_error: str | None = None,
    ) -> None:
        self.refuse = refuse or set()
        self.stop = stop
        self.copy_error = copy_error
        self.sent: list[str] = []
        self.copies: list[tuple] = []

    def configured(self) -> bool:
        return True

    def send(self, *, recipient: str, subject: str, body: str) -> str | None:
        if recipient in self.refuse:
            raise MailFailure("The mail server refused this recipient.", stop=self.stop)
        self.sent.append(recipient)
        return self.copy_error

    def save_copy(self, *, recipient: str, subject: str, body: str, sent_at) -> None:
        self.copies.append((recipient, subject, body, sent_at))


class GateMailer:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.sent: list[str] = []

    def configured(self) -> bool:
        return True

    def send(self, *, recipient: str, subject: str, body: str) -> str | None:
        self.started.set()
        assert self.release.wait(5)
        self.sent.append(recipient)
        return None

    def save_copy(self, *, recipient: str, subject: str, body: str, sent_at) -> None:
        return None


class Clock:
    def __init__(self, db=None, pause_on_sleep: bool = False) -> None:
        self.current = datetime(2026, 1, 1, tzinfo=UTC)
        self.sleeps: list[float] = []
        self.db = db
        self.pause_on_sleep = pause_on_sleep
        self.paused = False

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)
        if self.pause_on_sleep and not self.paused and self.db is not None:
            self.paused = True
            await self.db.campaigns.update_one({}, {"$set": {"control": "pause"}})


def _payload(**extra) -> dict:
    body = {
        "logins": ["ada"],
        "subject": "Hello {{ first_name }}",
        "body": "Your username is {{ login }}.",
        "hourly_limit": 10,
        "delay_min_seconds": 0,
        "delay_max_seconds": 0,
    }
    body.update(extra)
    return body


@asynccontextmanager
async def _http(app):
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def _drain(app, campaign_id: str) -> None:
    task = app.state.campaigns._tasks.get(campaign_id)
    if task is not None:
        await asyncio.wait_for(task, timeout=5)


async def test_missing_email_is_skipped_and_a_refusal_continues(db) -> None:
    await db.candidates.insert_many(
        [
            _candidate("ada", "ada@example.com", github_user_id=7, name="Ada Lovelace"),
            _candidate("linus", None, github_user_id=9, name="Linus"),
            _candidate("bad", "bad@example.com", github_user_id=8, name="Bad"),
        ]
    )
    mailer = FakeMailer(refuse={"bad@example.com"})
    app = create_app(settings=Settings(database_name="fetchiq_test"), db=db, mailer=mailer)
    async with _http(app) as client:
        created = await client.post(
            "/api/campaigns",
            json=_payload(logins=["ada", "linus", "bad"]),
        )
        assert created.status_code == 201
        body = created.json()
        assert body["counts"]["skipped"] == 1
        await _drain(app, body["id"])
        finished = await client.get(f"/api/campaigns/{body['id']}")
    assert finished.json()["status"] == "succeeded"
    assert finished.json()["counts"] == {"queued": 0, "sent": 1, "failed": 1, "skipped": 1}
    assert mailer.sent == ["ada@example.com"]
    ada = await db.candidates.find_one({"login": "ada"})
    assert ada["outreach_status"] == "message_sent"
    refused = await db.campaign_recipients.find_one({"login": "bad"})
    assert refused["status"] == "failed"


async def test_authentication_failure_stops_before_the_next_recipient(db) -> None:
    await db.candidates.insert_many(
        [
            _candidate("ada", "ada@example.com", github_user_id=7),
            _candidate("grace", "grace@example.com", github_user_id=8),
        ]
    )
    mailer = FakeMailer(refuse={"ada@example.com"}, stop=True)
    app = create_app(settings=Settings(database_name="fetchiq_test"), db=db, mailer=mailer)
    async with _http(app) as client:
        created = await client.post(
            "/api/campaigns",
            json=_payload(logins=["ada", "grace"]),
        )
        assert created.status_code == 201
        await _drain(app, created.json()["id"])
        finished = await client.get(f"/api/campaigns/{created.json()['id']}")
    assert finished.json()["status"] == "failed"
    grace = await db.campaign_recipients.find_one({"login": "grace"})
    assert grace["status"] == "queued"


async def test_unknown_placeholder_and_missing_mail_are_rejected(db) -> None:
    await db.candidates.insert_one(_candidate("ada", "ada@example.com"))
    plain = create_app(
        settings=Settings(
            database_name="fetchiq_test",
            smtp_username="",
            smtp_password="",
            smtp_from="",
        ),
        db=db,
    )
    async with _http(plain) as client:
        missing = await client.post("/api/campaigns", json=_payload())
    assert missing.status_code == 422
    assert missing.json()["detail"] == "Mail is not configured."
    assert await db.campaigns.count_documents({}) == 0

    app = create_app(
        settings=Settings(database_name="fetchiq_test"),
        db=db,
        mailer=FakeMailer(),
    )
    async with _http(app) as client:
        unknown = await client.post(
            "/api/campaigns",
            json=_payload(subject="Hello {{ frist_name }}"),
        )
    assert unknown.status_code == 422
    assert "frist_name" in unknown.json()["detail"]
    assert await db.campaigns.count_documents({}) == 0


async def test_create_returns_before_the_send_finishes(db) -> None:
    await db.candidates.insert_one(_candidate("ada", "ada@example.com", name="Ada Lovelace"))
    mailer = GateMailer()
    app = create_app(settings=Settings(database_name="fetchiq_test"), db=db, mailer=mailer)
    async with _http(app) as client:
        created = await client.post("/api/campaigns", json=_payload())
        assert created.status_code == 201
        assert created.json()["status"] == "running"
        assert created.json()["counts"]["sent"] == 0
        mailer.release.set()
        await _drain(app, created.json()["id"])
    assert mailer.sent == ["ada@example.com"]


async def test_hourly_limit_waits_for_the_next_message(db) -> None:
    await db.candidates.insert_many(
        [
            _candidate("ada", "ada@example.com", github_user_id=7),
            _candidate("grace", "grace@example.com", github_user_id=8),
        ]
    )
    clock = Clock()
    mailer = FakeMailer()
    runner = CampaignRunner(db, mailer, sleeper=clock.sleep, clock=clock.now, rng=lambda: 0)
    app = create_app(
        settings=Settings(database_name="fetchiq_test"),
        db=db,
        mailer=mailer,
        campaigns=runner,
    )
    async with _http(app) as client:
        created = await client.post(
            "/api/campaigns",
            json=_payload(logins=["ada", "grace"], hourly_limit=1),
        )
        assert created.status_code == 201
        await _drain(app, created.json()["id"])
        finished = await client.get(f"/api/campaigns/{created.json()['id']}")
    assert finished.json()["counts"]["sent"] == 2
    assert mailer.sent == ["ada@example.com", "grace@example.com"]
    assert sum(clock.sleeps) >= 3600


async def test_pause_during_the_delay_leaves_the_rest_queued(db) -> None:
    await db.candidates.insert_many(
        [
            _candidate("ada", "ada@example.com", github_user_id=7),
            _candidate("grace", "grace@example.com", github_user_id=8),
        ]
    )
    clock = Clock(db, pause_on_sleep=True)
    mailer = FakeMailer()
    runner = CampaignRunner(db, mailer, sleeper=clock.sleep, clock=clock.now, rng=lambda: 0)
    app = create_app(
        settings=Settings(database_name="fetchiq_test"),
        db=db,
        mailer=mailer,
        campaigns=runner,
    )
    async with _http(app) as client:
        created = await client.post(
            "/api/campaigns",
            json=_payload(logins=["ada", "grace"], delay_min_seconds=10, delay_max_seconds=10),
        )
        assert created.status_code == 201
        await _drain(app, created.json()["id"])
        finished = await client.get(f"/api/campaigns/{created.json()['id']}")
    assert finished.json()["status"] == "paused"
    assert mailer.sent == ["ada@example.com"]
    grace = await db.campaign_recipients.find_one({"login": "grace"})
    assert grace["status"] == "queued"


async def test_interrupted_send_is_not_retried(db) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    await db.campaigns.insert_one(
        {
            "_id": "camp-1",
            "subject": "Hello",
            "body": "Hi",
            "hourly_limit": 10,
            "delay_min_seconds": 0,
            "delay_max_seconds": 0,
            "status": "running",
            "control": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    await db.campaign_recipients.insert_one(
        {
            "_id": "row-1",
            "campaign_id": "camp-1",
            "position": 0,
            "login": "ada",
            "name": "Ada",
            "to_email": "ada@example.com",
            "status": "sending",
            "error": None,
            "sent_at": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    runner = CampaignRunner(db, FakeMailer())
    await runner.resume_running()
    await _drain_runner(runner, "camp-1")
    row = await db.campaign_recipients.find_one({"_id": "row-1"})
    campaign = await db.campaigns.find_one({"_id": "camp-1"})
    assert row["status"] == "failed"
    assert row["error"] == INTERRUPTED
    assert campaign["status"] == "succeeded"
    assert runner.mailer.sent == []


async def test_failed_sent_copy_still_counts_as_sent(db) -> None:
    await db.candidates.insert_one(_candidate("ada", "ada@example.com", name="Ada Lovelace"))
    mailer = FakeMailer(copy_error="Could not save the message in Sent.")
    app = create_app(settings=Settings(database_name="fetchiq_test"), db=db, mailer=mailer)
    async with _http(app) as client:
        created = await client.post("/api/campaigns", json=_payload())
        assert created.status_code == 201
        await _drain(app, created.json()["id"])
        finished = await client.get(f"/api/campaigns/{created.json()['id']}")
    assert finished.json()["counts"]["sent"] == 1
    row = await db.campaign_recipients.find_one({"login": "ada"})
    assert row["status"] == "sent"
    assert row["copy_error"] == "Could not save the message in Sent."
    assert row.get("copied_at") is None
    ada = await db.candidates.find_one({"login": "ada"})
    assert ada["outreach_status"] == "message_sent"
    assert mailer.sent == ["ada@example.com"]


async def test_startup_files_an_existing_sent_message_once(db) -> None:
    sent_at = datetime(2026, 2, 1, tzinfo=UTC)
    now = datetime(2026, 2, 2, tzinfo=UTC)
    await db.campaigns.insert_one(
        {
            "_id": "camp-sent",
            "subject": "Hello {{ first_name }}",
            "body": "Hi {{ login }}",
            "status": "succeeded",
            "created_at": now,
            "updated_at": now,
        }
    )
    await db.campaign_recipients.insert_one(
        {
            "_id": "row-sent",
            "campaign_id": "camp-sent",
            "position": 0,
            "login": "ada",
            "name": "Ada Lovelace",
            "to_email": "ada@example.com",
            "status": "sent",
            "error": None,
            "sent_at": sent_at,
            "copied_at": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    mailer = FakeMailer()
    app = create_app(settings=Settings(database_name="fetchiq_test"), db=db, mailer=mailer)
    async with app.router.lifespan_context(app):
        assert mailer.sent == []
        assert len(mailer.copies) == 1
        recipient, subject, body, copied_sent_at = mailer.copies[0]
        assert recipient == "ada@example.com"
        assert subject == "Hello Ada"
        assert body == "Hi ada"
        assert copied_sent_at is not None
        stored = await db.campaign_recipients.find_one({"_id": "row-sent"})
        assert stored["copied_at"] is not None
        assert stored["copy_error"] is None
    mailer.copies.clear()
    async with app.router.lifespan_context(app):
        assert mailer.sent == []
        assert mailer.copies == []


async def _drain_runner(runner: CampaignRunner, campaign_id: str) -> None:
    task = runner._tasks.get(campaign_id)
    if task is not None:
        await asyncio.wait_for(task, timeout=5)
