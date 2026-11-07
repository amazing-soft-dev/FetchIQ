"""A Hacker News search as a job: read threads, store posts, look up linked GitHub profiles.

The job document is a ``search_jobs`` row with ``kind: "hackernews"``. Its
``hackernews_criteria`` says how many monthly threads to read and which posts
to keep. Two phases run in order, each resumable from the checkpoint:

``threads``
    The newest ``months_back`` "Who wants to be hired?" threads, one at a
    time, comments in id order. Posts without a contact are skipped, posts
    outside the filters are counted as filtered, the rest go through
    ``app.hackernews.store_contact`` with this job's id. Per-thread tallies are
    kept on the job under ``threads``.
``github_lookup``
    When enabled, every contact this job stored that links a GitHub profile
    and has no personal email is read from the GitHub user API, then its
    profile README, for a name, company, account age and an email. The same
    "personal wins" rule decides the primary address.

Control (pause, cancel) is checked between posts and between profiles, as the
GitHub pipeline does. GitHub rate limits wait and retry the same profile.
"""

import asyncio
import logging
import re
from typing import Any

import httpx

from app.contacts import filter_fields, is_blank, merge_profile_fields
from app.counts import credit_candidate
from app.database import Database
from app.db import utcnow
from app.discovery import new_job_document
from app.enrichment import TWITTER_NAME, EnrichmentService, upsert_evidence
from app.extract import classify_url, normalize_email
from app.github import RateLimited, rate_limit_budget, wait_out_rate_limit
from app.hackernews import (
    SOURCE,
    DeveloperContact,
    HackerNewsClient,
    Thread,
    choose_public_email,
    parse_comment,
    place_only,
    store_contact,
)
from app.lease import heartbeat
from app.locations import normalize_location
from app.models import HackerNewsCriteria

log = logging.getLogger(__name__)

KIND = "hackernews"
# How many posts are read between two looks at the job's control field.
CONTROL_EVERY = 25
_REMOTE_YES = re.compile(r"^(?:yes|y\b|only|remote|open|prefer|hybrid)", re.IGNORECASE)


def new_hackernews_job(criteria: HackerNewsCriteria) -> dict[str, Any]:
    document = new_job_document(None, kind=KIND)
    document["hackernews_criteria"] = criteria.model_dump()
    document["threads"] = []
    document["github_lookups"] = {"done": 0, "missing": 0, "errors": 0}
    return document


def is_remote(value: str | None) -> bool:
    """True when a ``Remote:`` line says yes, remote only, or open to remote."""

    text = " ".join((value or "").split())
    return bool(text) and _REMOTE_YES.match(text) is not None


def matches_criteria(contact: DeveloperContact, criteria: HackerNewsCriteria) -> bool:
    """Whether a post passes the optional filters. Empty filters keep everything."""

    if criteria.countries:
        country = normalize_location(place_only(contact.location_raw)).country
        if country not in criteria.countries:
            return False
    if criteria.remote_only and not is_remote(contact.remote):
        return False
    if criteria.keywords:
        haystack = " ".join([*contact.technologies, contact.text]).casefold()
        if not any(keyword.casefold() in haystack for keyword in criteria.keywords):
            return False
    return True


def _thread_report(thread: Thread, posts: int) -> dict[str, Any]:
    return {
        "id": thread.id,
        "title": thread.title,
        "url": thread.url,
        "posts": posts,
        "stored": 0,
        "merged": 0,
        "unchanged": 0,
        "filtered": 0,
        "skipped": 0,
    }


class HackerNewsJobService:
    def __init__(self, db: Database, github, sleeper=None, hn_client=None) -> None:
        self.db = db
        self.github = github
        self.sleeper = sleeper or asyncio.sleep
        self._hn_client: httpx.AsyncClient | None = hn_client
        self._enrichment = EnrichmentService(db, github, sleeper=sleeper)

    async def run(self, job_id: str) -> str:
        """Run both phases. Return ``done``, ``paused`` or ``cancelled``."""

        job = await self.db.search_jobs.find_one({"_id": job_id})
        if job is None:
            raise KeyError(job_id)
        criteria = HackerNewsCriteria(**(job.get("hackernews_criteria") or {}))
        checkpoint = dict(job.get("checkpoint") or {})
        phase = checkpoint.get("phase") or "threads"

        if phase == "threads":
            async with HackerNewsClient(self._hn_client) as hn:
                outcome = await self._read_threads(job_id, criteria, checkpoint, hn)
            if outcome != "done":
                return outcome
            phase = "github_lookup" if criteria.github_lookup else "done"
            checkpoint = {"phase": phase}
            await self._save_checkpoint(job_id, checkpoint)

        if phase == "github_lookup":
            outcome = await self._lookup_github(job_id, checkpoint)
            if outcome != "done":
                return outcome
            checkpoint = {"phase": "done"}
            await self._save_checkpoint(job_id, checkpoint)
        return "done"

    # --- phase 1: threads ---------------------------------------------------

    async def _read_threads(
        self,
        job_id: str,
        criteria: HackerNewsCriteria,
        checkpoint: dict,
        hn: HackerNewsClient,
    ) -> str:
        checkpoint["phase"] = "threads"
        threads = await hn.hiring_threads(limit=criteria.months_back)
        start = int(checkpoint.get("thread_index") or 0)
        for index in range(start, len(threads)):
            thread = threads[index]
            comments = sorted(await hn.comments(thread), key=lambda comment: comment.id)
            report = await self._report_for(job_id, thread, len(comments))
            last = checkpoint.get("last_comment_id") if index == start else None
            seen = 0
            for comment in comments:
                if isinstance(last, int) and comment.id <= last:
                    continue
                if seen % CONTROL_EVERY == 0:
                    control = await self._control(job_id)
                    if control:
                        await self._write_report(job_id, report)
                        await self._finish_control(job_id, control, checkpoint)
                        return control
                seen += 1
                await self._read_post(job_id, criteria, thread, comment, report)
                checkpoint["last_comment_id"] = comment.id
                if seen % CONTROL_EVERY == 0:
                    await self._write_report(job_id, report)
                    await self._save_checkpoint(job_id, checkpoint)
            await self._write_report(job_id, report)
            checkpoint["thread_index"] = index + 1
            checkpoint["last_comment_id"] = None
            await self._save_checkpoint(job_id, checkpoint)
            await self._event(
                job_id,
                "thread_read",
                f"Read {thread.title}",
                {key: report[key] for key in ("id", "posts", "stored", "merged", "filtered")},
            )
            log.info("hacker news thread read", extra={"job_id": job_id, **report})
        return "done"

    async def _read_post(
        self,
        job_id: str,
        criteria: HackerNewsCriteria,
        thread: Thread,
        comment,
        report: dict[str, Any],
    ) -> None:
        contact = parse_comment(comment)
        if not contact.emails and not contact.urls and not contact.github_login:
            report["skipped"] += 1
            return
        if not matches_criteria(contact, criteria):
            report["filtered"] += 1
            return
        stored = await store_contact(self.db, contact, thread, job_id=job_id)
        if stored.outcome in ("fresh", "has_email", "skipped"):
            report["unchanged"] += 1
            return
        candidate = await self.db.candidates.find_one({"_id": stored.candidate_id})
        if candidate is None:
            return
        if stored.outcome == "merged":
            report["merged"] += 1
            owner = candidate.get("job_id")
            if isinstance(owner, str):
                await credit_candidate(self.db, owner, candidate)
            return
        report["stored"] += 1
        if stored.moved and stored.previous_job_id:
            dec = {f"counts.{key}": -value for key, value in stored.moved.items() if value}
            if dec:
                await self.db.search_jobs.update_one({"_id": stored.previous_job_id}, {"$inc": dec})
        await self.db.search_jobs.update_one({"_id": job_id}, {"$inc": {"counts.discovered": 1}})
        await credit_candidate(self.db, job_id, {**candidate, "counted": {}}, enriched=1)

    async def _report_for(self, job_id: str, thread: Thread, posts: int) -> dict[str, Any]:
        job = await self.db.search_jobs.find_one({"_id": job_id}, {"threads": 1})
        for report in (job or {}).get("threads") or []:
            if report.get("id") == thread.id:
                report["posts"] = posts
                return report
        return _thread_report(thread, posts)

    async def _write_report(self, job_id: str, report: dict[str, Any]) -> None:
        job = await self.db.search_jobs.find_one({"_id": job_id}, {"threads": 1})
        reports = [
            item for item in (job or {}).get("threads") or [] if item.get("id") != report["id"]
        ]
        reports.append(report)
        reports.sort(key=lambda item: item.get("id") or 0, reverse=True)
        await self.db.search_jobs.update_one(
            {"_id": job_id}, {"$set": {"threads": reports, "updated_at": utcnow()}}
        )

    # --- phase 2: GitHub lookup ---------------------------------------------

    async def _lookup_github(self, job_id: str, checkpoint: dict) -> str:
        checkpoint["phase"] = "github_lookup"
        while True:
            control = await self._control(job_id)
            if control:
                await self._finish_control(job_id, control, checkpoint)
                return control
            candidate = await self._next_lookup(job_id, checkpoint.get("last_lookup_user_id"))
            if candidate is None:
                return "done"
            try:
                result = await self._lookup_one(job_id, candidate)
            except RateLimited as exc:
                control = await self._wait_through_rate_limit(job_id, checkpoint, exc)
                if control:
                    await self._finish_control(job_id, control, checkpoint)
                    return control
                continue
            except (RuntimeError, httpx.ConnectError):
                raise
            except Exception as exc:
                result = "errors"
                await self.db.candidates.update_one(
                    {"_id": candidate["_id"]}, {"$set": {"github_lookup": "error"}}
                )
                await self._event(
                    job_id,
                    "lookup_error",
                    f"Could not read GitHub profile {candidate.get('github_login')}: {exc}"[:500],
                    {"login": candidate.get("github_login")},
                )
            await self.db.search_jobs.update_one(
                {"_id": job_id},
                {
                    "$inc": {
                        f"github_lookups.{result}": 1,
                        **({"counts.errors": 1} if result == "errors" else {}),
                    }
                },
            )
            checkpoint["last_lookup_user_id"] = candidate["github_user_id"]
            await self._save_checkpoint(job_id, checkpoint)

    async def _next_lookup(self, job_id: str, after_id: int | None) -> dict | None:
        query: dict[str, Any] = {
            "job_id": job_id,
            "source": SOURCE,
            "github_login": {"$nin": [None, ""]},
            "personal": {"$ne": True},
        }
        if after_id is not None:
            query["github_user_id"] = {"$gt": after_id}
        return await self.db.candidates.find_one(query, sort=[("github_user_id", 1)])

    async def _lookup_one(self, job_id: str, candidate: dict) -> str:
        """Read one linked GitHub profile. Return ``done``, ``missing`` or ``errors``."""

        login = str(candidate["github_login"])
        user_id = candidate["github_user_id"]
        response = await self.github.get(f"/users/{login}")
        if response.status_code == 404:
            await self.db.candidates.update_one(
                {"_id": candidate["_id"]}, {"$set": {"github_lookup": "missing"}}
            )
            return "missing"
        if response.status_code >= 400:
            raise RuntimeError(f"profile request returned {response.status_code}")
        profile = response.json()
        profile_url = profile.get("html_url") or f"https://github.com/{login}"
        emails: list[str] = []

        api_email = normalize_email(profile.get("email") or "")
        if api_email:
            emails.append(api_email)
            await upsert_evidence(
                self.db,
                github_user_id=user_id,
                kind="email",
                normalized_value=api_email,
                raw_value=profile.get("email") or api_email,
                source_url=profile_url,
            )
        blog = classify_url(profile.get("blog") or "", prefer_website=True)
        if blog:
            await upsert_evidence(
                self.db,
                github_user_id=user_id,
                kind=blog[0],
                normalized_value=blog[1],
                raw_value=profile.get("blog") or blog[1],
                source_url=profile_url,
            )
        twitter = (profile.get("twitter_username") or "").strip().lstrip("@")
        if TWITTER_NAME.fullmatch(twitter):
            await upsert_evidence(
                self.db,
                github_user_id=user_id,
                kind="x",
                normalized_value=f"https://x.com/{twitter}",
                raw_value=twitter,
                source_url=profile_url,
            )
        if not emails:
            readme_text, readme_url = await self._enrichment._profile_readme(login)
            if readme_text and readme_url:
                await self._enrichment._store_contacts(user_id, readme_text, readme_url, emails)

        evidence = [row async for row in self.db.contact_evidence.find({"github_user_id": user_id})]
        location = normalize_location(profile.get("location"))
        incoming = {
            "name": profile.get("name"),
            "company": profile.get("company"),
            "location_raw": profile.get("location"),
            "country": location.country,
            "location_confidence": location.confidence if profile.get("location") else None,
            "github_created_at": profile.get("created_at"),
            "public_repos_count": profile.get("public_repos"),
        }
        chosen = choose_public_email(candidate.get("public_email"), emails)
        filled, email_changed = merge_profile_fields(candidate, incoming, chosen)
        if is_blank(candidate.get("github_url")):
            filled["github_url"] = profile_url
        final_email = filled.get("public_email", candidate.get("public_email"))
        filled.update(filter_fields({"public_email": final_email}, evidence))
        filled["github_lookup"] = "done"
        filled["github_profile_url"] = profile_url
        filled["last_checked_at"] = utcnow()
        filled["updated_at"] = filled["last_checked_at"]
        update: dict[str, Any] = {"$set": filled, "$addToSet": {"sources": "github"}}
        if email_changed and candidate.get("outreach_status"):
            update["$unset"] = {"outreach_status": ""}
        await self.db.candidates.update_one({"_id": candidate["_id"]}, update)
        await credit_candidate(self.db, job_id, candidate, evidence)
        return "done"

    # --- shared --------------------------------------------------------------

    async def _control(self, job_id: str) -> str | None:
        job = await self.db.search_jobs.find_one({"_id": job_id}, {"control": 1})
        control = (job or {}).get("control")
        if control == "cancel":
            return "cancelled"
        if control == "pause":
            return "paused"
        return None

    async def _finish_control(self, job_id: str, status: str, checkpoint: dict) -> None:
        await self._save_checkpoint(job_id, checkpoint)
        await self.db.search_jobs.update_one(
            {"_id": job_id},
            {"$set": {"status": status, "control": None, "updated_at": utcnow()}},
        )
        await self._event(job_id, status, f"Job {status}", {})
        log.info(
            "job control applied",
            extra={"job_id": job_id, "status": status, "stage": checkpoint.get("phase")},
        )

    async def _wait_through_rate_limit(
        self, job_id: str, checkpoint: dict, exc: RateLimited
    ) -> str | None:
        await self._save_checkpoint(job_id, checkpoint)
        await self._record_rate_limit(job_id, exc, exc.retry_after)
        await self._event(
            job_id,
            "rate_limited",
            "Waiting for the GitHub rate limit to reset",
            {"retry_after_seconds": exc.retry_after},
        )
        log.warning(
            "github rate limit reached",
            extra={
                "job_id": job_id,
                "stage": "github_lookup",
                "retry_after_seconds": exc.retry_after,
            },
        )

        async def stopped() -> str | None:
            await heartbeat(self.db.search_jobs, job_id)
            return await self._control(job_id)

        reason, remaining = await wait_out_rate_limit(exc.retry_after, self.sleeper, stopped)
        await self._record_rate_limit(job_id, exc, remaining)
        return reason

    async def _record_rate_limit(self, job_id: str, exc: RateLimited, seconds_left: float) -> None:
        await self.db.search_jobs.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "request_budget": rate_limit_budget(exc, seconds_left=seconds_left),
                    "updated_at": utcnow(),
                }
            },
        )

    async def _save_checkpoint(self, job_id: str, checkpoint: dict) -> None:
        await self.db.search_jobs.update_one(
            {"_id": job_id},
            {"$set": {"checkpoint": checkpoint, "updated_at": utcnow()}},
        )
        await heartbeat(self.db.search_jobs, job_id)

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
