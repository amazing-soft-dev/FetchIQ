"""In-process job runner. One document, one task, checkpoints in MongoDB."""

import asyncio

from app.db import utcnow
from app.discovery import DiscoveryService
from app.enrichment import EnrichmentService
from app.github import RATE_LIMIT_STEP_SECONDS, GitHubClient


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
                }
            },
        )
        current = self._tasks.get(job_id)
        if current is not None and not current.done():
            return
        self._tasks[job_id] = asyncio.create_task(self._run(job_id))

    async def resume_running(self) -> None:
        """Continue jobs left running when the process stopped before enrichment."""

        cursor = self.db.search_jobs.find({"status": "running"}, {"_id": 1})
        async for job in cursor:
            job_id = job["_id"]
            current = self._tasks.get(job_id)
            if current is not None and not current.done():
                continue
            self._tasks[job_id] = asyncio.create_task(self._run(job_id))

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
                return False
            step = min(RATE_LIMIT_STEP_SECONDS, remaining)
            await self.sleeper(step)
            remaining = max(0.0, remaining - step)
        if wait > 0:
            await self.db.search_jobs.update_one(
                {"_id": job_id},
                {"$set": {"request_budget.retry_after_seconds": None, "updated_at": utcnow()}},
            )
        return True

    async def _run(self, job_id: str) -> None:
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
        except Exception as exc:
            message = str(exc)[:500]
            status = "incomplete" if message.startswith("Could not resolve") else "failed"
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
