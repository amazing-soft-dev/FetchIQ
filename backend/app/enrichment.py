"""Enrich candidates from the profile API and public READMEs.

Commit patches, mail headers, and noreply placeholders are never read. If the
profile email is null and no address is published in the profile or README, the
email stays empty.
"""

import base64
import re
from datetime import timedelta
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.db import utcnow
from app.extract import classify_url, extract_published_contacts, normalize_email
from app.github import RateLimited
from app.locations import normalize_location

MAX_MATCHING_REPOS = 5
MAX_README_REPOS = 15
MAX_README_BYTES = 100_000
FRESHNESS = timedelta(days=7)
TWITTER_NAME = re.compile(r"[A-Za-z0-9_]{1,15}\Z")
PROFILE_FIELDS = (
    "name",
    "bio",
    "company",
    "public_email",
    "location_raw",
    "country",
    "us_state",
    "location_confidence",
    "github_created_at",
    "public_repos_count",
    "primary_languages",
    "matched_keywords",
    "relevant_repository_urls",
    "collection_status",
    "last_checked_at",
)


async def upsert_evidence(
    db,
    *,
    github_user_id: int,
    kind: str,
    normalized_value: str,
    raw_value: str,
    source_url: str,
) -> None:
    now = utcnow()
    try:
        await db.contact_evidence.update_one(
            {
                "github_user_id": github_user_id,
                "kind": kind,
                "normalized_value": normalized_value,
            },
            {
                "$setOnInsert": {
                    "github_user_id": github_user_id,
                    "kind": kind,
                    "normalized_value": normalized_value,
                    "raw_value": raw_value,
                    "source_url": source_url,
                    "validation_status": "valid",
                    "created_at": now,
                },
                "$set": {"last_seen_at": now},
            },
            upsert=True,
        )
    except DuplicateKeyError:
        return


class EnrichmentService:
    def __init__(self, db, github) -> None:
        self.db = db
        self.github = github

    async def run(self, job_id: str) -> str:
        job = await self.db.search_jobs.find_one({"_id": job_id})
        if job is None:
            raise KeyError(job_id)
        checkpoint = dict(job.get("checkpoint") or {})
        checkpoint["phase"] = "enrichment"
        keywords = [item.casefold() for item in (job.get("criteria") or {}).get("keywords") or []]
        await self._save_checkpoint(job_id, checkpoint)

        while True:
            control = await self._control(job_id)
            if control:
                await self._finish_control(job_id, control, checkpoint)
                return control
            candidate = await self._next_candidate(job_id, checkpoint.get("last_enriched_user_id"))
            if candidate is None:
                checkpoint["phase"] = "enrichment_done"
                await self._save_checkpoint(job_id, checkpoint)
                return "done"
            try:
                await self._enrich_one(job_id, candidate, keywords)
            except RateLimited as exc:
                await self._pause_for_rate_limit(job_id, checkpoint, exc)
                return "paused"
            checkpoint["last_enriched_user_id"] = candidate["github_user_id"]
            await self._save_checkpoint(job_id, checkpoint)

    async def _enrich_one(self, job_id: str, candidate: dict, keywords: list[str]) -> None:
        fresh = await self._fresh_copy(candidate)
        if fresh is not None:
            await self._copy_fresh(job_id, candidate, fresh)
            return

        login = candidate["login"]
        profile_url = candidate.get("profile_url") or f"https://github.com/{login}"
        response = await self.github.get(f"/users/{login}")
        if response.status_code == 404:
            await self._miss(job_id, candidate, "profile_missing", "Profile was not found")
            return
        if response.status_code >= 400:
            await self._miss(
                job_id, candidate, "error", f"Profile request returned {response.status_code}"
            )
            await self._mark_incomplete(job_id, f"profile request returned {response.status_code}")
            return

        profile = response.json()
        emails: list[str] = []
        api_email = normalize_email(profile.get("email") or "")
        if api_email:
            emails.append(api_email)
            await upsert_evidence(
                self.db,
                github_user_id=candidate["github_user_id"],
                kind="email",
                normalized_value=api_email,
                raw_value=profile.get("email") or api_email,
                source_url=profile_url,
            )

        await self._store_contacts(
            candidate["github_user_id"],
            profile.get("bio") or "",
            profile_url,
            emails,
        )
        blog = classify_url(profile.get("blog") or "", prefer_website=True)
        if blog:
            await upsert_evidence(
                self.db,
                github_user_id=candidate["github_user_id"],
                kind=blog[0],
                normalized_value=blog[1],
                raw_value=profile.get("blog") or blog[1],
                source_url=profile_url,
            )
        twitter = (profile.get("twitter_username") or "").strip().lstrip("@")
        if TWITTER_NAME.fullmatch(twitter):
            await upsert_evidence(
                self.db,
                github_user_id=candidate["github_user_id"],
                kind="x",
                normalized_value=f"https://x.com/{twitter}",
                raw_value=twitter,
                source_url=profile_url,
            )

        readme_text, readme_url = await self._profile_readme(login)
        if readme_text and readme_url:
            await self._store_contacts(candidate["github_user_id"], readme_text, readme_url, emails)
        elif readme_url is None and readme_text is None:
            await self._event(
                job_id, "readme_miss", f"No profile README for {login}", {"login": login}
            )

        languages, matched, repo_urls = await self._matching_repos(
            candidate["github_user_id"],
            login,
            keywords,
            emails,
        )
        location = normalize_location(profile.get("location"))
        now = utcnow()
        await self.db.candidates.update_one(
            {"_id": candidate["_id"]},
            {
                "$set": {
                    "name": profile.get("name"),
                    "bio": profile.get("bio"),
                    "company": profile.get("company"),
                    "public_email": emails[0] if emails else None,
                    "location_raw": profile.get("location"),
                    "country": location.country,
                    "us_state": location.us_state,
                    "location_confidence": location.confidence,
                    "github_created_at": profile.get("created_at"),
                    "public_repos_count": profile.get("public_repos"),
                    "primary_languages": languages,
                    "matched_keywords": matched,
                    "relevant_repository_urls": repo_urls,
                    "enrichment_status": "enriched",
                    "collection_status": "enriched",
                    "last_checked_at": now,
                    "updated_at": now,
                }
            },
        )
        await self.db.search_jobs.update_one({"_id": job_id}, {"$inc": {"counts.enriched": 1}})

    async def _store_contacts(
        self,
        github_user_id: int,
        text: str,
        source_url: str,
        emails: list[str],
    ) -> None:
        published = extract_published_contacts(text)
        for email in published.emails:
            if email not in emails:
                emails.append(email)
            await upsert_evidence(
                self.db,
                github_user_id=github_user_id,
                kind="email",
                normalized_value=email,
                raw_value=email,
                source_url=source_url,
            )
        for kind, url in published.urls:
            await upsert_evidence(
                self.db,
                github_user_id=github_user_id,
                kind=kind,
                normalized_value=url,
                raw_value=url,
                source_url=source_url,
            )

    async def _profile_readme(self, login: str) -> tuple[str | None, str | None]:
        return await self._readme(login, login)

    async def _matching_repos(
        self,
        github_user_id: int,
        login: str,
        keywords: list[str],
        emails: list[str],
    ) -> tuple[list[str], list[str], list[str]]:
        response = await self.github.get(
            f"/users/{login}/repos",
            params={"per_page": 100, "type": "owner", "sort": "updated"},
        )
        if response.status_code >= 400:
            return [], [], []
        owned = [
            repo
            for repo in response.json() or []
            if isinstance(repo, dict)
            and not repo.get("fork")
            and not repo.get("archived")
            and repo.get("name")
            and repo.get("html_url")
        ]
        owned.sort(key=lambda repo: repo.get("pushed_at") or "", reverse=True)
        owned.sort(key=lambda repo: 0 if _keyword_hits(repo, keywords) else 1)

        languages: list[str] = []
        matched: list[str] = []
        urls: list[str] = []
        email_count = len(emails)
        scanned = 0
        for repo in owned:
            hits = _keyword_hits(repo, keywords)
            record_match = bool(hits) and len(urls) < MAX_MATCHING_REPOS
            scan_readme = scanned < MAX_README_REPOS and (
                record_match or len(emails) == email_count
            )
            if not record_match and not scan_readme:
                continue
            if record_match:
                language = repo.get("language")
                if language and language not in languages:
                    languages.append(language)
                for hit in hits:
                    if hit not in matched:
                        matched.append(hit)
                urls.append(repo["html_url"])
            if not scan_readme:
                continue
            scanned += 1
            readme, source = await self._repo_readme(login, repo["name"])
            if readme and source:
                await self._store_contacts(github_user_id, readme, source, emails)
        return languages, matched, urls

    async def _repo_readme(self, login: str, repo: str) -> tuple[str | None, str | None]:
        return await self._readme(login, repo)

    async def _readme(self, login: str, repo: str) -> tuple[str | None, str | None]:
        """Read a public README from the default branch, then main, then master."""

        response = await self.github.get(f"/repos/{login}/{repo}/readme")
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, dict):
                return _decode_readme(payload, login, repo, "HEAD")
        elif response.status_code >= 400 and response.status_code != 404:
            return None, None
        for ref in ("main", "master"):
            contents = await self.github.get(
                f"/repos/{login}/{repo}/contents/README.md",
                params={"ref": ref},
            )
            if contents.status_code == 404:
                continue
            if contents.status_code >= 400:
                return None, None
            payload = contents.json()
            if not isinstance(payload, dict):
                continue
            return _decode_readme(payload, login, repo, ref)
        return None, None

    async def _fresh_copy(self, candidate: dict) -> dict | None:
        cutoff = utcnow() - FRESHNESS
        return await self.db.candidates.find_one(
            {
                "github_user_id": candidate["github_user_id"],
                "job_id": {"$ne": candidate["job_id"]},
                "enrichment_status": "enriched",
                "last_checked_at": {"$gte": cutoff},
            },
            sort=[("last_checked_at", -1)],
        )

    async def _copy_fresh(self, job_id: str, candidate: dict, fresh: dict) -> None:
        now = utcnow()
        copied = {field: fresh.get(field) for field in PROFILE_FIELDS}
        copied["enrichment_status"] = "enriched"
        copied["updated_at"] = now
        await self.db.candidates.update_one({"_id": candidate["_id"]}, {"$set": copied})
        await self.db.search_jobs.update_one({"_id": job_id}, {"$inc": {"counts.enriched": 1}})
        await self._event(
            job_id,
            "reused",
            f"Reused a recent public profile for {candidate['login']}",
            {"github_user_id": candidate["github_user_id"]},
        )

    async def _next_candidate(self, job_id: str, after_id: int | None) -> dict | None:
        query: dict[str, Any] = {"job_id": job_id, "enrichment_status": "pending"}
        if after_id is not None:
            query["github_user_id"] = {"$gt": after_id}
        return await self.db.candidates.find_one(query, sort=[("github_user_id", 1)])

    async def _miss(self, job_id: str, candidate: dict, status: str, message: str) -> None:
        now = utcnow()
        await self.db.candidates.update_one(
            {"_id": candidate["_id"]},
            {
                "$set": {
                    "enrichment_status": status,
                    "collection_status": status,
                    "last_checked_at": now,
                    "updated_at": now,
                    "public_email": None,
                }
            },
        )
        await self.db.search_jobs.update_one({"_id": job_id}, {"$inc": {"counts.errors": 1}})
        await self._event(job_id, status, message, {"login": candidate.get("login")})

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


def _decode_readme(payload: dict, owner: str, repo: str, ref: str) -> tuple[str | None, str | None]:
    source = payload.get("html_url") or f"https://github.com/{owner}/{repo}/blob/{ref}/README.md"
    size = int(payload.get("size") or 0)
    if size > MAX_README_BYTES:
        return None, source
    content = payload.get("content") or ""
    if payload.get("encoding") not in {None, "base64"}:
        return None, source
    try:
        decoded = base64.b64decode(content)
    except (ValueError, TypeError):
        return None, source
    if len(decoded) > MAX_README_BYTES:
        return None, source
    return decoded.decode("utf-8", errors="replace"), source


def _keyword_hits(repo: dict, keywords: list[str]) -> list[str]:
    language = (repo.get("language") or "").casefold()
    topics = {str(topic).casefold() for topic in repo.get("topics") or []}
    hits: list[str] = []
    for keyword in keywords:
        if keyword == language or keyword in topics:
            hits.append(keyword)
    return hits
