import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import lease
from app.campaigns import CampaignBusy, CampaignRunner, queue_campaign
from app.db import utcnow
from app.discovery import DiscoveryService, new_job_document
from app.lease import LEASE_SECONDS, WORKER_ID, claim_stale, heartbeat
from app.models import SearchCriteria
from app.runner import JobRunner


def _job(**fields) -> dict:
    document = new_job_document(SearchCriteria(countries=["Germany"]))
    document["status"] = "running"
    document.update(fields)
    return document


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _stale():
    return utcnow() - timedelta(seconds=LEASE_SECONDS + 5)


@pytest.fixture(autouse=True)
def _fresh_heartbeats():
    lease._last_beat.clear()
    yield
    lease._last_beat.clear()


async def test_claim_takes_only_running_documents_with_a_lapsed_lease(db) -> None:
    fresh = _job(worker_id="other", heartbeat_at=utcnow())
    stale = _job(worker_id="other", heartbeat_at=_stale())
    legacy = _job()
    paused = _job(status="paused", worker_id="other", heartbeat_at=_stale())
    await db.search_jobs.insert_many([fresh, stale, legacy, paused])

    assert await claim_stale(db.search_jobs, fresh["_id"]) is None
    taken = await claim_stale(db.search_jobs, stale["_id"])
    assert taken["worker_id"] == WORKER_ID
    assert await claim_stale(db.search_jobs, legacy["_id"]) is not None
    assert await claim_stale(db.search_jobs, paused["_id"]) is None

    stored = await db.search_jobs.find_one({"_id": fresh["_id"]})
    assert stored["worker_id"] == "other"


async def test_two_concurrent_claims_yield_one_winner(db) -> None:
    job = _job(worker_id="dead", heartbeat_at=_stale())
    await db.search_jobs.insert_one(job)
    first, second = await asyncio.gather(
        claim_stale(db.search_jobs, job["_id"], worker_id="a"),
        claim_stale(db.search_jobs, job["_id"], worker_id="b"),
    )
    winners = [claim for claim in (first, second) if claim is not None]
    assert len(winners) == 1
    stored = await db.search_jobs.find_one({"_id": job["_id"]})
    assert stored["worker_id"] == winners[0]["worker_id"]


async def test_kick_leases_the_job_to_this_worker(db, monkeypatch) -> None:
    started: list[str] = []

    async def fake_run(self, job_id: str) -> None:
        started.append(job_id)

    monkeypatch.setattr(JobRunner, "_run", fake_run)
    job = _job(status="queued")
    await db.search_jobs.insert_one(job)
    runner = JobRunner(db, None)
    await runner.kick(job["_id"])
    await runner._tasks[job["_id"]]

    stored = await db.search_jobs.find_one({"_id": job["_id"]})
    assert stored["status"] == "running"
    assert stored["worker_id"] == WORKER_ID
    assert stored["heartbeat_at"] is not None
    assert started == [job["_id"]]


async def test_resume_claims_stale_jobs_and_leaves_held_ones(db, monkeypatch) -> None:
    started: list[str] = []

    async def fake_run(self, job_id: str) -> None:
        started.append(job_id)

    monkeypatch.setattr(JobRunner, "_run", fake_run)
    held = _job(worker_id="other", heartbeat_at=utcnow())
    lapsed = _job(worker_id="other", heartbeat_at=_stale())
    await db.search_jobs.insert_many([held, lapsed])

    runner = JobRunner(db, None)
    await runner.resume_running()
    await asyncio.gather(*runner._tasks.values())

    assert started == [lapsed["_id"]]
    assert (await db.search_jobs.find_one({"_id": lapsed["_id"]}))["worker_id"] == WORKER_ID
    assert (await db.search_jobs.find_one({"_id": held["_id"]}))["worker_id"] == "other"


async def test_sweeper_picks_up_a_job_whose_lease_lapses_later(db, monkeypatch) -> None:
    started: list[str] = []

    async def fake_run(self, job_id: str) -> None:
        started.append(job_id)

    monkeypatch.setattr(JobRunner, "_run", fake_run)
    runner = JobRunner(db, None)
    runner.start_sweeper(interval=0.01)
    try:
        job = _job(worker_id="dead", heartbeat_at=_stale())
        await db.search_jobs.insert_one(job)
        for _ in range(50):
            await asyncio.sleep(0.01)
            if started:
                break
    finally:
        await runner.stop_sweeper()
    assert started == [job["_id"]]
    assert runner._sweeper is None


async def test_checkpoint_saves_refresh_only_this_workers_heartbeat(db) -> None:
    mine = _job(worker_id=WORKER_ID, heartbeat_at=_stale())
    theirs = _job(worker_id="other", heartbeat_at=_stale())
    await db.search_jobs.insert_many([mine, theirs])
    service = DiscoveryService(db, None)

    await service._save_checkpoint(mine["_id"], {"phase": "discovery"})
    await service._save_checkpoint(theirs["_id"], {"phase": "discovery"})

    refreshed = await db.search_jobs.find_one({"_id": mine["_id"]})
    untouched = await db.search_jobs.find_one({"_id": theirs["_id"]})
    assert _aware(refreshed["heartbeat_at"]) > _stale()
    assert _aware(untouched["heartbeat_at"]) < _stale() + timedelta(seconds=1)


async def test_heartbeat_is_throttled_per_document(db) -> None:
    job = _job(worker_id=WORKER_ID, heartbeat_at=_stale())
    await db.search_jobs.insert_one(job)
    assert await heartbeat(db.search_jobs, job["_id"]) is True
    assert await heartbeat(db.search_jobs, job["_id"]) is False
    assert await heartbeat(db.search_jobs, job["_id"], force=True) is True


def _campaign(**fields) -> dict:
    now = utcnow()
    document = {
        "_id": fields.pop("_id", "camp-1"),
        "subject": "Hi",
        "body": "Hello {{ first_name }}",
        "hourly_limit": 30,
        "delay_min_seconds": 0,
        "delay_max_seconds": 0,
        "status": "running",
        "control": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    document.update(fields)
    return document


def _draft() -> SimpleNamespace:
    return SimpleNamespace(
        logins=["ada"],
        subject="Hi",
        body="Hello",
        hourly_limit=30,
        delay_min_seconds=0,
        delay_max_seconds=0,
    )


async def test_queueing_is_blocked_only_by_this_workers_or_unclaimed_campaigns(db) -> None:
    await db.candidates.insert_one(
        {"login": "ada", "github_user_id": 7, "public_email": "ada@example.com"}
    )
    await db.campaigns.insert_one(
        _campaign(_id="elsewhere", worker_id="other", heartbeat_at=utcnow())
    )
    created = await queue_campaign(db, _draft())
    assert created["status"] == "queued"

    with pytest.raises(CampaignBusy):
        await queue_campaign(db, _draft())

    await db.campaigns.delete_one({"_id": created["_id"]})
    await db.campaigns.insert_one(_campaign(_id="mine", status="paused", worker_id=WORKER_ID))
    with pytest.raises(CampaignBusy):
        await queue_campaign(db, _draft())


async def test_campaign_resume_claims_only_lapsed_leases(db, monkeypatch) -> None:
    started: list[str] = []

    async def fake_run(self, campaign_id: str) -> None:
        started.append(campaign_id)

    monkeypatch.setattr(CampaignRunner, "_run", fake_run)
    await db.campaigns.insert_many(
        [
            _campaign(_id="held", worker_id="other", heartbeat_at=utcnow()),
            _campaign(_id="lapsed", worker_id="other", heartbeat_at=_stale()),
            _campaign(_id="legacy"),
        ]
    )
    runner = CampaignRunner(db, None)
    await runner.resume_running()
    await asyncio.gather(*runner._tasks.values())
    assert sorted(started) == ["lapsed", "legacy"]
    assert (await db.campaigns.find_one({"_id": "held"}))["worker_id"] == "other"
    assert (await db.campaigns.find_one({"_id": "lapsed"}))["worker_id"] == WORKER_ID


async def test_shutdown_cancels_in_flight_runs_and_keeps_the_lease(db, monkeypatch) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_run(self, job_id: str) -> None:
        started.set()
        await release.wait()

    monkeypatch.setattr(JobRunner, "_run", fake_run)
    job = _job(status="queued")
    await db.search_jobs.insert_one(job)
    runner = JobRunner(db, None)
    runner.start_sweeper(interval=0.01)
    await runner.kick(job["_id"])
    await asyncio.wait_for(started.wait(), timeout=1)

    await runner.shutdown()

    assert runner._tasks[job["_id"]].cancelled()
    assert runner._sweeper is None
    stored = await db.search_jobs.find_one({"_id": job["_id"]})
    assert stored["status"] == "running"
    assert stored["worker_id"] == WORKER_ID
