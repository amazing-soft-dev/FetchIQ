"""In-process job runner. One document, one task, checkpoints in MongoDB."""

import asyncio
import logging
from contextlib import suppress

from app.db import utcnow
from app.discovery import DiscoveryService
from app.enrichment import EnrichmentService
from app.github import RATE_LIMIT_STEP_SECONDS, GitHubClient
from app.lease import SWEEP_SECONDS, claim_stale, forget, heartbeat, lease_fields

log = logging.getLogger(__name__)


def _network_hold(job: dict) -> bool:
    """A failed lookup can be resumed. The checkpoint is still on the job."""

    error = str(job.get("error") or "")
    return job.get("status") == "failed" and error.startswith("Could not resolve")


class JobRunner:
    def __init__(self, db, github: GitHubClient, sleeper=None) -> None:
        self.db = db
        self.github = github
        self.sleeper = sleeper or asyncio.sleep
        self._tasks: dict[str, asyncio.Task] = {}
        self._sweeper: asyncio.Task | None = None

    async def kick(self, job_id: str) -> None:
        job = await self.db.search_jobs.find_one({"_id": job_id})
        if job is None:
            raise KeyError(job_id)
        if job["status"] in {"succeeded", "failed", "cancelled"} and not _network_hold(job):
            raise RuntimeError("job is finished")
        await self.db.search_jobs.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": "running",
                    "control": None,
                    "error": None,
                    "updated_at": utcnow(),
                    **lease_fields(),
                }
            },
        )
        log.info("job running", extra={"job_id": job_id, "previous_status": job["status"]})
        current = self._tasks.get(job_id)
        if current is not None and not current.done():
            return
        self._tasks[job_id] = asyncio.create_task(self._run(job_id))

    async def resume_running(self) -> None:
        """Take over running jobs whose lease has lapsed. Held ones are left alone."""

        cursor = self.db.search_jobs.find({"status": "running"}, {"_id": 1})
        async for job in cursor:
            job_id = job["_id"]
            current = self._tasks.get(job_id)
            if current is not None and not current.done():
                continue
            if await claim_stale(self.db.search_jobs, job_id) is None:
                continue
            log.info("job resumed after lease lapse", extra={"job_id": job_id})
            self._tasks[job_id] = asyncio.create_task(self._run(job_id))

    def start_sweeper(self, interval: float = SWEEP_SECONDS) -> None:
        """Look for lapsed leases every ``interval`` seconds until stopped."""

        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep(interval))

    async def stop_sweeper(self) -> None:
        if self._sweeper is None:
            return
        self._sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await self._sweeper
        self._sweeper = None

    async def _sweep(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.resume_running()
            except Exception:
                log.exception("lease sweep failed")

    async def _control(self, job_id: str) -> str | None:
        job = await self.db.search_jobs.find_one({"_id": job_id}, {"control": 1})
        control = (job or {}).get("control")
        if control == "cancel":
            return "cancelled"
        if control == "pause":
            return "paused"
        return None

    async def _wait_for_budget(self, job_id: str) -> bool:
        """Sleep through a stored rate-limit window. False when the user stops the job."""

        job = await self.db.search_jobs.find_one({"_id": job_id}, {"request_budget": 1})
        budget = (job or {}).get("request_budget") or {}
        wait = 0.0
        remaining_requests = budget.get("remaining")
        reset_at = budget.get("reset_at")
        if remaining_requests == 0 and reset_at is not None:
            if reset_at.tzinfo is None:
                reset_at = reset_at.replace(tzinfo=utcnow().tzinfo)
            remaining_reset = (reset_at - utcnow()).total_seconds()
            if remaining_reset > 0:
                wait = remaining_reset
        if wait == 0.0:
            retry = budget.get("retry_after_seconds")
            if isinstance(retry, int | float) and retry > 0:
                wait = float(retry)
        remaining = wait
        while remaining > 0:
            control = await self._control(job_id)
            if control:
                await self.db.search_jobs.update_one(
                    {"_id": job_id},
                    {
                        "$set": {
                            "status": control,
                            "control": None,
                            "request_budget.retry_after_seconds": remaining,
                            "updated_at": utcnow(),
                        }
                    },
                )
                log.info(
                    "job stopped while waiting for the rate limit",
                    extra={"job_id": job_id, "status": control, "seconds_left": remaining},
                )
                return False
            step = min(RATE_LIMIT_STEP_SECONDS, remaining)
            await self.sleeper(step)
            remaining = max(0.0, remaining - step)
            await heartbeat(self.db.search_jobs, job_id)
        if wait > 0:
            await self.db.search_jobs.update_one(
                {"_id": job_id},
                {"$set": {"request_budget.retry_after_seconds": None, "updated_at": utcnow()}},
            )
        return True

    async def _run(self, job_id: str) -> None:
        try:
            await self._run_leased(job_id)
        finally:
            forget(job_id)

    async def _run_leased(self, job_id: str) -> None:
        try:
            if not await self._wait_for_budget(job_id):
                return
            outcome = await DiscoveryService(self.db, self.github, sleeper=self.sleeper).run(job_id)
            if outcome != "done":
                return
            if not await self._wait_for_budget(job_id):
                return
            outcome = await EnrichmentService(self.db, self.github, sleeper=self.sleeper).run(
                job_id
            )
            if outcome != "done":
                return
            job = await self.db.search_jobs.find_one({"_id": job_id}, {"incomplete": 1})
            status = "incomplete" if job and job.get("incomplete") else "succeeded"
            await self.db.search_jobs.update_one(
                {"_id": job_id},
                {"$set": {"status": status, "error": None, "updated_at": utcnow()}},
            )
            log.info("job finished", extra={"job_id": job_id, "status": status})
        except Exception as exc:
            message = str(exc)[:500]
            status = "incomplete" if message.startswith("Could not resolve") else "failed"
            log.exception("job failed", extra={"job_id": job_id, "status": status})
            await self.db.search_jobs.update_one(
                {"_id": job_id},
                {"$set": {"status": status, "error": message, "updated_at": utcnow()}},
            )
            await self.db.job_events.insert_one(
                {
                    "job_id": job_id,
                    "created_at": utcnow(),
                    "kind": "error",
                    "message": str(exc)[:500],
                    "detail": {},
                }
            )
