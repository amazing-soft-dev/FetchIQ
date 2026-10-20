"""In-process job runner. One document, one task, checkpoints in MongoDB."""

import asyncio

from app.db import utcnow
from app.discovery import DiscoveryService
from app.github import GitHubClient


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
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            raise RuntimeError("job is finished")
        await self.db.search_jobs.update_one(
            {"_id": job_id},
            {"$set": {"status": "running", "control": None, "updated_at": utcnow()}},
        )
        current = self._tasks.get(job_id)
        if current is not None and not current.done():
            return
        self._tasks[job_id] = asyncio.create_task(self._run(job_id))

    async def _wait_for_budget(self, job_id: str) -> None:
        job = await self.db.search_jobs.find_one({"_id": job_id}, {"request_budget": 1})
        budget = (job or {}).get("request_budget") or {}
        wait = 0.0
        retry = budget.get("retry_after_seconds")
        if isinstance(retry, int | float) and retry > 0:
            wait = float(retry)
        reset_at = budget.get("reset_at")
        if reset_at is not None:
            if reset_at.tzinfo is None:
                reset_at = reset_at.replace(tzinfo=utcnow().tzinfo)
            remaining = (reset_at - utcnow()).total_seconds()
            if remaining > 0:
                wait = max(wait, remaining)
        if wait > 0:
            await self.sleeper(wait)

    async def _run(self, job_id: str) -> None:
        try:
            await self._wait_for_budget(job_id)
            outcome = await DiscoveryService(self.db, self.github).run(job_id)
            if outcome != "done":
                return
            job = await self.db.search_jobs.find_one({"_id": job_id}, {"incomplete": 1})
            status = "incomplete" if job and job.get("incomplete") else "succeeded"
            await self.db.search_jobs.update_one(
                {"_id": job_id},
                {"$set": {"status": status, "updated_at": utcnow()}},
            )
        except Exception as exc:
            await self.db.search_jobs.update_one(
                {"_id": job_id},
                {"$set": {"status": "failed", "error": str(exc)[:500], "updated_at": utcnow()}},
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
