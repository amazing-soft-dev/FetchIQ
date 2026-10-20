"""GitHub user search with date splits, checkpoints, and unique-index dedup."""

from datetime import date
from typing import Any
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from app.dates import account_age_window, split_date_window
from app.db import utcnow
from app.geo import UNITED_STATES
from app.github import RateLimited, parse_next_link, read_budget
from app.models import SearchCriteria

SEARCH_RESULT_CAP = 1000
PAGE_SIZE = 100
MAX_PAGES = 10


def quote_term(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def location_seeds(criteria: SearchCriteria) -> list[str]:
    seeds: list[str] = []
    for country in criteria.countries:
        if country == UNITED_STATES and criteria.us_states:
            seeds.extend(criteria.us_states)
        else:
            seeds.append(country)
    return seeds


def build_user_query(criteria: SearchCriteria, start: date, end: date) -> str:
    parts: list[str] = []
    seeds = location_seeds(criteria)
    if seeds:
        locations = " OR ".join(f"location:{quote_term(seed)}" for seed in seeds)
        parts.append(f"({locations})" if len(seeds) > 1 else locations)
    parts.append(f"created:{start.isoformat()}..{end.isoformat()}")
    parts.append("type:user")
    for keyword in criteria.keywords:
        if any(character.isspace() for character in keyword):
            parts.append(quote_term(keyword))
        else:
            parts.append(keyword)
    return " ".join(parts)


def new_job_document(criteria: SearchCriteria) -> dict[str, Any]:
    now = utcnow()
    return {
        "_id": str(uuid4()),
        "criteria": criteria.model_dump(),
        "status": "queued",
        "control": None,
        "incomplete": False,
        "incomplete_reasons": [],
        "checkpoint": None,
        "request_budget": {
            "limit": None,
            "remaining": None,
            "reset_at": None,
            "retry_after_seconds": None,
        },
        "counts": {"discovered": 0, "enriched": 0, "errors": 0},
        "error": None,
        "created_at": now,
        "updated_at": now,
    }


def _window(start: date, end: date) -> dict[str, Any]:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "next_url": None,
        "pages_fetched": 0,
    }


async def upsert_candidate(db, job_id: str, item: dict[str, Any]) -> str:
    """Insert a candidate once per job and GitHub user id.

    A duplicate-key error means another slice already stored this person.
    """

    now = utcnow()
    login = item["login"]
    profile_url = item.get("html_url") or f"https://github.com/{login}"
    try:
        result = await db.candidates.update_one(
            {"job_id": job_id, "github_user_id": item["id"]},
            {
                "$setOnInsert": {
                    "job_id": job_id,
                    "github_user_id": item["id"],
                    "login": login,
                    "profile_url": profile_url,
                    "discovered_at": now,
                    "enrichment_status": "pending",
                    "collection_status": "discovered",
                    "name": None,
                    "bio": None,
                    "company": None,
                    "public_email": None,
                    "location_raw": None,
                    "country": None,
                    "us_state": None,
                    "location_confidence": None,
                    "github_created_at": None,
                    "public_repos_count": None,
                    "primary_languages": [],
                    "matched_keywords": [],
                    "relevant_repository_urls": [],
                    "last_checked_at": None,
                },
                "$set": {"updated_at": now},
            },
            upsert=True,
        )
    except DuplicateKeyError:
        return "already_stored"
    if result.upserted_id is not None:
        await db.search_jobs.update_one({"_id": job_id}, {"$inc": {"counts.discovered": 1}})
        return "inserted"
    return "already_stored"


class DiscoveryService:
    def __init__(self, db, github, *, today: date | None = None) -> None:
        self.db = db
        self.github = github
        self.today = today or utcnow().date()

    async def run(self, job_id: str) -> str:
        job = await self.db.search_jobs.find_one({"_id": job_id})
        if job is None:
            raise KeyError(job_id)
        criteria = SearchCriteria.model_validate(job["criteria"])
        checkpoint = job.get("checkpoint")
        if checkpoint and checkpoint.get("phase") == "discovery_done":
            return "done"
        if not checkpoint or not checkpoint.get("windows"):
            start, end = account_age_window(
                min_years=criteria.account_age.min_years,
                min_months=criteria.account_age.min_months,
                max_years=criteria.account_age.max_years,
                max_months=criteria.account_age.max_months,
                today=self.today,
            )
            checkpoint = {"phase": "discovery", "windows": [_window(start, end)]}
            await self._save_checkpoint(job_id, checkpoint)

        discovered = int((job.get("counts") or {}).get("discovered") or 0)
        limit = criteria.candidate_limit

        while True:
            control = await self._control(job_id)
            if control:
                await self._finish_control(job_id, control, checkpoint)
                return control
            if discovered >= limit:
                return await self._complete(job_id, checkpoint)
            windows = list(checkpoint.get("windows") or [])
            if not windows:
                return await self._complete(job_id, checkpoint)

            window = dict(windows[0])
            start = date.fromisoformat(window["start"])
            end = date.fromisoformat(window["end"])
            try:
                response = await self._fetch(window, criteria, start, end)
            except RateLimited as exc:
                await self._pause_for_rate_limit(job_id, checkpoint, exc)
                return "paused"

            await self._store_budget(job_id, response, retry_after_seconds=None)
            if response.status_code >= 400:
                await self._mark_incomplete(
                    job_id, f"github search returned {response.status_code}"
                )
                await self._event(
                    job_id,
                    "error",
                    f"GitHub search returned {response.status_code}",
                    {"start": window["start"], "end": window["end"]},
                )
                checkpoint["windows"] = windows[1:]
                await self._save_checkpoint(job_id, checkpoint)
                continue

            payload = response.json()
            total = int(payload.get("total_count") or 0)
            if window.get("next_url") is None and total > SEARCH_RESULT_CAP:
                halves = split_date_window(start, end)
                if halves is not None:
                    left, right = halves
                    checkpoint["windows"] = [_window(*left), _window(*right), *windows[1:]]
                    await self._save_checkpoint(job_id, checkpoint)
                    await self._event(
                        job_id,
                        "split",
                        "Split a date window that exceeded 1000 results",
                        {
                            "total_count": total,
                            "start": window["start"],
                            "end": window["end"],
                        },
                    )
                    continue
                await self._mark_incomplete(job_id, "single-day window exceeded 1000 results")

            for item in payload.get("items") or []:
                if discovered >= limit:
                    break
                if not isinstance(item, dict) or "id" not in item or "login" not in item:
                    continue
                outcome = await upsert_candidate(self.db, job_id, item)
                if outcome == "inserted":
                    discovered += 1

            if discovered >= limit:
                return await self._complete(job_id, checkpoint)

            next_url = parse_next_link(response.headers.get("link"))
            current_url = str(response.url)
            pages = int(window["pages_fetched"]) + 1
            if next_url and next_url == current_url:
                await self._mark_incomplete(job_id, "repeated page")
                checkpoint["windows"] = windows[1:]
            elif next_url and pages >= MAX_PAGES:
                await self._mark_incomplete(job_id, "pagination cap")
                checkpoint["windows"] = windows[1:]
            elif next_url:
                window["next_url"] = next_url
                window["pages_fetched"] = pages
                checkpoint["windows"] = [window, *windows[1:]]
            else:
                checkpoint["windows"] = windows[1:]
            await self._save_checkpoint(job_id, checkpoint)

    async def _fetch(self, window: dict, criteria: SearchCriteria, start: date, end: date):
        if window.get("next_url"):
            return await self.github.get(window["next_url"])
        query = build_user_query(criteria, start, end)
        return await self.github.get("/search/users", params={"q": query, "per_page": PAGE_SIZE})

    async def _control(self, job_id: str) -> str | None:
        job = await self.db.search_jobs.find_one({"_id": job_id}, {"control": 1})
        control = (job or {}).get("control")
        if control == "cancel":
            return "cancelled"
        if control == "pause":
            return "paused"
        return None

    async def _complete(self, job_id: str, checkpoint: dict) -> str:
        checkpoint["windows"] = []
        checkpoint["phase"] = "discovery_done"
        await self._save_checkpoint(job_id, checkpoint)
        return "done"

    async def _finish_control(self, job_id: str, status: str, checkpoint: dict) -> None:
        await self._save_checkpoint(job_id, checkpoint)
        await self.db.search_jobs.update_one(
            {"_id": job_id},
            {"$set": {"status": status, "control": None, "updated_at": utcnow()}},
        )
        await self._event(job_id, status, f"Job {status}", {})

    async def _pause_for_rate_limit(self, job_id: str, checkpoint: dict, exc: RateLimited) -> None:
        await self._save_checkpoint(job_id, checkpoint)
        await self.db.search_jobs.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": "paused",
                    "control": None,
                    "request_budget": {
                        "limit": exc.limit,
                        "remaining": exc.remaining,
                        "reset_at": exc.reset_at,
                        "retry_after_seconds": exc.retry_after,
                    },
                    "updated_at": utcnow(),
                }
            },
        )
        await self._event(
            job_id,
            "rate_limited",
            "Paused until the GitHub rate limit resets",
            {"retry_after_seconds": exc.retry_after},
        )

    async def _store_budget(
        self, job_id: str, response, *, retry_after_seconds: float | None
    ) -> None:
        budget = read_budget(response).as_document(retry_after_seconds=retry_after_seconds)
        await self.db.search_jobs.update_one(
            {"_id": job_id},
            {"$set": {"request_budget": budget, "updated_at": utcnow()}},
        )

    async def _mark_incomplete(self, job_id: str, reason: str) -> None:
        await self.db.search_jobs.update_one(
            {"_id": job_id},
            {
                "$set": {"incomplete": True, "updated_at": utcnow()},
                "$addToSet": {"incomplete_reasons": reason},
            },
        )

    async def _save_checkpoint(self, job_id: str, checkpoint: dict) -> None:
        await self.db.search_jobs.update_one(
            {"_id": job_id},
            {"$set": {"checkpoint": checkpoint, "updated_at": utcnow()}},
        )

    async def _event(self, job_id: str, kind: str, message: str, detail: dict) -> None:
        await self.db.job_events.insert_one(
            {
                "job_id": job_id,
                "created_at": utcnow(),
                "kind": kind,
                "message": message,
                "detail": detail,
            }
        )
