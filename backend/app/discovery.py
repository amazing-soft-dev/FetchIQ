"""GitHub user search with date splits, checkpoints, and one row per username."""

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from app.contacts import is_blank
from app.counts import candidate_share, zero_share
from app.dates import account_age_window, split_date_window
from app.db import utcnow
from app.enrichment import EnrichmentService
from app.geo import UNITED_STATES
from app.github import (
    RateLimited,
    parse_next_link,
    rate_limit_budget,
    read_budget,
    wait_out_rate_limit,
)
from app.lease import heartbeat
from app.models import SearchCriteria
from app.places import KIND_ORDER, Place, places_for_country
from app.slots import (
    can_fetch,
    equal_shares,
    note_page_yield,
    only_duplicates,
    reallocate,
    taken,
)

log = logging.getLogger(__name__)

SEARCH_RESULT_CAP = 1000
PAGE_SIZE = 100
MAX_PAGES = 10
FRESH_PROFILE = timedelta(days=14)
INCOMPLETE_RETRIES = 3


def quote_term(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def location_seeds(criteria: SearchCriteria) -> list[str]:
    """Cities, then regions and aliases, then the country name.

    Selected US states replace a bare United States search. The list is the
    one a job snapshots, so later edits to the place file do not reshuffle it.
    """

    seeds: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        key = name.casefold()
        if not name or key in seen:
            return
        seen.add(key)
        seeds.append(name)

    for country in criteria.countries:
        if country == UNITED_STATES and criteria.us_states:
            selected = set(criteria.us_states)
            cities = [
                place
                for place in places_for_country(country)
                if place.kind == "city" and place.tier != "overflow" and place.region in selected
            ]
            for place in sorted(cities, key=lambda place: (place.rank, place.name)):
                add(place.name)
            for state in criteria.us_states:
                add(state)
            continue
        ordered = sorted(
            places_for_country(country),
            key=lambda place: (KIND_ORDER.get(place.kind, 9), place.rank, place.name),
        )
        for place in ordered:
            if place.tier == "overflow":
                continue
            add(place.name)
        add(country)
    return seeds


def build_user_query(criteria: SearchCriteria, start: date, end: date, location: str) -> str:
    """One location per query. GitHub user search returns nothing for a location OR."""

    parts = [
        f"location:{quote_term(location)}",
        f"created:{start.isoformat()}..{end.isoformat()}",
        "type:user",
    ]
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
        "counts": {
            "discovered": 0,
            "enriched": 0,
            "errors": 0,
            "emails": 0,
            "linkedin_without_email": 0,
        },
        "funnel": {
            "inserted": 0,
            "updated": 0,
            "duplicate": 0,
            "fresh": 0,
            "has_email": 0,
            "moved_in": 0,
            "moved_out": 0,
        },
        "stop_reason": None,
        "overlap_preview": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }


def _window(start: date, end: date, location: str) -> dict[str, Any]:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "location": location,
        "next_url": None,
        "pages_fetched": 0,
    }


def _allocator_checkpoint(criteria: SearchCriteria, today: date) -> dict[str, Any]:
    start, end = account_age_window(
        min_years=criteria.account_age.min_years,
        min_months=criteria.account_age.min_months,
        max_years=criteria.account_age.max_years,
        max_months=criteria.account_age.max_months,
        today=today,
    )
    seeds = location_seeds(criteria)
    shares = equal_shares(criteria.candidate_limit, len(seeds))
    start_text, end_text = start.isoformat(), end.isoformat()
    return {
        "phase": "discovery",
        "mode": "allocate",
        "locations": seeds,
        "overflow": overflow_names(criteria),
        "overflow_index": 0,
        "start": start_text,
        "end": end_text,
        "cursors": [
            _blank_cursor(name, start_text, end_text, share, "primary")
            for name, share in zip(seeds, shares, strict=True)
        ],
        "caps": [],
        "window": None,
        "pending": [],
    }


def _place_checkpoint(criteria: SearchCriteria, today: date) -> dict[str, Any]:
    start, end = account_age_window(
        min_years=criteria.account_age.min_years,
        min_months=criteria.account_age.min_months,
        max_years=criteria.account_age.max_years,
        max_months=criteria.account_age.max_months,
        today=today,
    )
    locations = location_seeds(criteria)
    window = _window(start, end, locations[0]) if locations else None
    return {
        "phase": "discovery",
        "locations": locations,
        "index": 0,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "window": window,
        "pending": [],
    }


def _advance_location(checkpoint: dict[str, Any]) -> None:
    """Finish the current slice, then a split half, then the next place."""

    pending = list(checkpoint.get("pending") or [])
    locations = list(checkpoint.get("locations") or [])
    index = int(checkpoint.get("index") or 0)
    if pending:
        nxt = pending.pop(0)
        checkpoint["pending"] = pending
        checkpoint["window"] = _window(
            date.fromisoformat(nxt["start"]),
            date.fromisoformat(nxt["end"]),
            locations[index],
        )
        return
    index += 1
    checkpoint["index"] = index
    checkpoint["pending"] = []
    if index >= len(locations):
        checkpoint["window"] = None
        return
    checkpoint["window"] = _window(
        date.fromisoformat(checkpoint["start"]),
        date.fromisoformat(checkpoint["end"]),
        locations[index],
    )


_OBSOLETE_CANDIDATE_FIELDS = {
    "us_state": "",
    "primary_languages": "",
    "matched_keywords": "",
    "relevant_repository_urls": "",
}


def _pending_candidate(job_id: str, item: dict[str, Any], profile_url: str, now) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "github_user_id": item["id"],
        "login": item["login"],
        "profile_url": profile_url,
        "enrichment_status": "pending",
        "collection_status": "discovered",
        "name": None,
        "bio": None,
        "company": None,
        "public_email": None,
        "location_raw": None,
        "country": None,
        "utc_offset": None,
        "location_confidence": None,
        "github_created_at": None,
        "public_repos_count": None,
        "last_checked_at": None,
        "updated_at": now,
    }


def _checked_at(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def upsert_candidate(
    db, job_id: str, item: dict[str, Any]
) -> tuple[str, str | None, dict[str, int] | None]:
    """Insert a username once. Return the outcome, the previous job id, and the moved share.

    A new login is ``inserted``. An older profile with no public email is ``updated``
    and queued for a refresh. A repeat in this job is ``duplicate``. A profile checked
    within two weeks is ``fresh``. An older profile that already has a public email is
    ``has_email``. The page that fetched the user records the slot; this function does
    not write the job counters. For an ``updated`` profile the third value is what it
    added to the previous job's counters, so the page tally can move it over.
    """

    now = utcnow()
    login = item["login"]
    profile_url = item.get("html_url") or f"https://github.com/{login}"
    existing = await db.candidates.find_one({"login": login})
    if existing is not None and existing.get("job_id") == job_id:
        await db.candidates.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "github_user_id": item["id"],
                    "profile_url": profile_url,
                    "updated_at": now,
                },
                "$unset": _OBSOLETE_CANDIDATE_FIELDS,
            },
        )
        return "duplicate", None, None
    if existing is not None:
        checked = _checked_at(existing.get("last_checked_at"))
        if checked is not None and checked >= now - FRESH_PROFILE:
            return "fresh", None, None
        if not is_blank(existing.get("public_email")):
            return "has_email", None, None
        source = existing.get("job_id")
        moved = candidate_share(existing)
        changes: dict[str, Any] = {
            "job_id": job_id,
            "enrichment_status": "merge",
            "updated_at": now,
        }
        if is_blank(existing.get("profile_url")):
            changes["profile_url"] = profile_url
        await db.candidates.update_one(
            {"_id": existing["_id"]},
            {"$set": changes, "$unset": _OBSOLETE_CANDIDATE_FIELDS},
        )
        return "updated", source if isinstance(source, str) else None, moved
    try:
        result = await db.candidates.update_one(
            {"login": login},
            {
                "$set": _pending_candidate(job_id, item, profile_url, now),
                "$setOnInsert": {"discovered_at": now},
                "$unset": _OBSOLETE_CANDIDATE_FIELDS,
            },
            upsert=True,
        )
    except DuplicateKeyError:
        return "duplicate", None, None
    if result.upserted_id is not None:
        return "inserted", None, None
    return "duplicate", None, None


class PageTally:
    def __init__(self) -> None:
        self.inserted = 0
        self.updated = 0
        self.duplicate = 0
        self.fresh = 0
        self.has_email = 0
        self.fetched = 0
        # Counter shares carried in from other jobs, and what each of them lost.
        self.moved_in = zero_share()
        self.moved_out: dict[str, dict[str, int]] = {}

    def add(
        self, outcome: str, source_job_id: str | None, moved: dict[str, int] | None = None
    ) -> None:
        self.fetched += 1
        if outcome == "inserted":
            self.inserted += 1
        elif outcome == "updated":
            self.updated += 1
            share = moved or candidate_share({})
            for key in self.moved_in:
                if key != "enriched":
                    self.moved_in[key] += share.get(key, 0)
            if source_job_id:
                lost = self.moved_out.setdefault(source_job_id, zero_share())
                for key in lost:
                    lost[key] += share.get(key, 0)
        elif outcome == "fresh":
            self.fresh += 1
        elif outcome == "has_email":
            self.has_email += 1
        else:
            self.duplicate += 1

    @property
    def slots(self) -> int:
        return self.inserted + self.updated


def slot_count(job: dict[str, Any]) -> int:
    funnel = job.get("funnel") or {}
    if "inserted" in funnel or "updated" in funnel:
        return int(funnel.get("inserted") or 0) + int(funnel.get("updated") or 0)
    return int((job.get("counts") or {}).get("discovered") or 0)


def _blank_cursor(location: str, start: str, end: str, share: int, wave: str) -> dict[str, Any]:
    return {
        "location": location,
        "wave": wave,
        "start": start,
        "end": end,
        "next_url": None,
        "pages_fetched": 0,
        "pending": [],
        "reported": None,
        "fetched": 0,
        "inserted": 0,
        "updated": 0,
        "duplicate": 0,
        "fresh": 0,
        "has_email": 0,
        "share": share,
        "exhausted": False,
        "slice_fetched": 0,
        "caps": [],
        "incomplete_tries": 0,
        "total_unknown": False,
        "noted": False,
        "zero_pages": 0,
    }


def overflow_names(criteria: SearchCriteria) -> list[str]:
    """Next cities by population, skipping seeds and cities outside a selected state."""

    primary = {name.casefold() for name in location_seeds(criteria)}
    ranked: list[Place] = []
    for country in criteria.countries:
        states = (
            set(criteria.us_states) if country == UNITED_STATES and criteria.us_states else None
        )
        for place in places_for_country(country):
            if place.tier != "overflow" or place.kind != "city":
                continue
            if states is not None and place.region not in states:
                continue
            if place.name.casefold() in primary:
                continue
            ranked.append(place)
    ranked.sort(key=lambda place: (-place.population, place.name))
    names: list[str] = []
    seen = set(primary)
    for place in ranked:
        key = place.name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(place.name)
    return names


def _apply_tally(cursor: dict[str, Any], tally: PageTally) -> None:
    cursor["fetched"] = int(cursor.get("fetched") or 0) + tally.fetched
    cursor["slice_fetched"] = int(cursor.get("slice_fetched") or 0) + tally.fetched
    cursor["inserted"] = int(cursor.get("inserted") or 0) + tally.inserted
    cursor["updated"] = int(cursor.get("updated") or 0) + tally.updated
    cursor["duplicate"] = int(cursor.get("duplicate") or 0) + tally.duplicate
    cursor["fresh"] = int(cursor.get("fresh") or 0) + tally.fresh
    cursor["has_email"] = int(cursor.get("has_email") or 0) + tally.has_email
    note_page_yield(cursor, fetched=tally.fetched, slots=tally.slots)


def _remember_reported(cursor: dict[str, Any], total: int, *, full: bool, unknown: bool) -> None:
    if unknown or not full or cursor.get("reported") is not None:
        return
    cursor["reported"] = total


def _advance_cursor(cursor: dict[str, Any]) -> None:
    pending = list(cursor.get("pending") or [])
    if pending:
        nxt = pending.pop(0)
        cursor["pending"] = pending
        cursor["start"] = nxt["start"]
        cursor["end"] = nxt["end"]
        cursor["next_url"] = None
        cursor["pages_fetched"] = 0
        cursor["slice_fetched"] = 0
        cursor["incomplete_tries"] = 0
        return
    cursor["pending"] = []
    cursor["exhausted"] = True
    cursor["next_url"] = None


def _window_from_cursor(cursor: dict[str, Any]) -> dict[str, Any]:
    return {
        "start": cursor["start"],
        "end": cursor["end"],
        "location": cursor["location"],
        "next_url": cursor.get("next_url"),
        "pages_fetched": int(cursor.get("pages_fetched") or 0),
    }


def _select_cursor(
    cursors: list[dict[str, Any]], *, stop_duplicates: bool
) -> dict[str, Any] | None:
    for cursor in cursors:
        if can_fetch(cursor, stop_duplicates=stop_duplicates):
            return cursor
        if stop_duplicates and not cursor.get("exhausted") and only_duplicates(cursor):
            cursor["exhausted"] = True
    return None


def _open_overflow(checkpoint: dict[str, Any], limit: int, slots: int) -> bool:
    names = list(checkpoint.get("overflow") or [])
    index = int(checkpoint.get("overflow_index") or 0)
    remaining = max(limit - slots, 0)
    while index < len(names) and remaining > 0:
        name = names[index]
        index += 1
        checkpoint["overflow_index"] = index
        if any(
            str(cursor.get("location")).casefold() == name.casefold()
            for cursor in checkpoint["cursors"]
        ):
            continue
        checkpoint["cursors"].append(
            _blank_cursor(name, checkpoint["start"], checkpoint["end"], remaining, "overflow")
        )
        return True
    checkpoint["overflow_index"] = index
    return False


class DiscoveryService:
    def __init__(self, db, github, *, today: date | None = None, sleeper=None) -> None:
        self.db = db
        self.github = github
        self.today = today or utcnow().date()
        self.sleeper = sleeper or asyncio.sleep
        self.enrichment = EnrichmentService(db, github, sleeper=self.sleeper)

    async def run(self, job_id: str) -> str:
        job = await self.db.search_jobs.find_one({"_id": job_id})
        if job is None:
            raise KeyError(job_id)
        criteria = SearchCriteria.model_validate(job["criteria"])
        checkpoint = job.get("checkpoint")
        phase = (checkpoint or {}).get("phase")
        if phase in {"discovery_done", "enrichment", "enrichment_done"}:
            return "done"
        if checkpoint and checkpoint.get("mode") == "allocate":
            return await self._drain_allocated(job_id, job, criteria, checkpoint)
        if checkpoint and "locations" not in checkpoint and checkpoint.get("windows") is not None:
            return await self._drain_windows(job_id, job, criteria, checkpoint)
        if checkpoint and "locations" in checkpoint:
            return await self._drain_locations(job_id, job, criteria, checkpoint)
        checkpoint = _allocator_checkpoint(criteria, self.today)
        await self._save_checkpoint(job_id, checkpoint)
        return await self._drain_allocated(job_id, job, criteria, checkpoint)

    async def _drain_windows(
        self, job_id: str, job: dict, criteria: SearchCriteria, checkpoint: dict
    ) -> str:
        """Finish a checkpoint saved before place lists existed."""

        if not checkpoint.get("windows"):
            start, end = account_age_window(
                min_years=criteria.account_age.min_years,
                min_months=criteria.account_age.min_months,
                max_years=criteria.account_age.max_years,
                max_months=criteria.account_age.max_months,
                today=self.today,
            )
            checkpoint = {
                "phase": "discovery",
                "windows": [_window(start, end, seed) for seed in location_seeds(criteria)],
            }
            await self._save_checkpoint(job_id, checkpoint)

        slots = slot_count(job)
        limit = criteria.candidate_limit

        while True:
            control = await self._control(job_id)
            if control:
                await self._finish_control(job_id, control, checkpoint)
                return control
            if slots >= limit:
                return await self._complete(job_id, checkpoint, "limit_reached")
            windows = list(checkpoint.get("windows") or [])
            if not windows:
                return await self._complete(job_id, checkpoint, "supply_exhausted")

            window = dict(windows[0])
            start = date.fromisoformat(window["start"])
            end = date.fromisoformat(window["end"])
            if not window.get("location"):
                expanded = [_window(start, end, seed) for seed in location_seeds(criteria)]
                checkpoint["windows"] = [*expanded, *windows[1:]]
                await self._save_checkpoint(job_id, checkpoint)
                continue
            try:
                response = await self._fetch(window, criteria, start, end)
            except RateLimited as exc:
                control = await self._wait_through_rate_limit(job_id, checkpoint, exc)
                if control:
                    await self._finish_control(job_id, control, checkpoint)
                    return control
                continue

            await self._store_budget(job_id, response, retry_after_seconds=None)
            if response.status_code >= 400:
                await self._mark_incomplete(
                    job_id, f"github search returned {response.status_code}"
                )
                await self._event(
                    job_id,
                    "error",
                    f"GitHub search returned {response.status_code}",
                    {
                        "location": window.get("location"),
                        "start": window["start"],
                        "end": window["end"],
                    },
                )
                checkpoint["windows"] = windows[1:]
                await self._save_checkpoint(job_id, checkpoint)
                continue

            payload = response.json()
            if payload.get("incomplete_results"):
                tries = int(window.get("incomplete_tries") or 0) + 1
                window["incomplete_tries"] = tries
                checkpoint["windows"] = [window, *windows[1:]]
                await self._save_checkpoint(job_id, checkpoint)
                if tries <= INCOMPLETE_RETRIES:
                    continue
                total = 0
            else:
                window["incomplete_tries"] = 0
                total = int(payload.get("total_count") or 0)
            capped = False
            if window.get("next_url") is None and total > SEARCH_RESULT_CAP:
                halves = split_date_window(start, end)
                if halves is not None:
                    left, right = halves
                    location = str(window["location"])
                    checkpoint["windows"] = [
                        _window(*left, location),
                        _window(*right, location),
                        *windows[1:],
                    ]
                    await self._save_checkpoint(job_id, checkpoint)
                    await self._event(
                        job_id,
                        "split",
                        "Split a date window that exceeded 1000 results",
                        {
                            "location": location,
                            "total_count": total,
                            "start": window["start"],
                            "end": window["end"],
                        },
                    )
                    continue
                await self._mark_slice(
                    job_id,
                    checkpoint,
                    window,
                    "single-day window exceeded 1000 results",
                    total,
                )
                capped = True

            tally = await self._tally_page(job_id, payload.get("items") or [])
            slots += tally.slots

            next_url = None if capped else parse_next_link(response.headers.get("link"))
            current_url = str(response.url)
            pages = int(window["pages_fetched"]) + 1
            if capped:
                checkpoint["windows"] = windows[1:]
            elif next_url and next_url == current_url:
                await self._mark_slice(job_id, checkpoint, window, "repeated page", total)
                checkpoint["windows"] = windows[1:]
            elif next_url and pages >= MAX_PAGES:
                await self._mark_slice(job_id, checkpoint, window, "pagination cap", total)
                checkpoint["windows"] = windows[1:]
            elif next_url:
                window["next_url"] = next_url
                window["pages_fetched"] = pages
                checkpoint["windows"] = [window, *windows[1:]]
            else:
                checkpoint["windows"] = windows[1:]
            await self._flush_tally(job_id, tally, checkpoint)
            if slots >= limit:
                stopped = await self._enrich_page(job_id)
                if stopped:
                    return stopped
                return await self._complete(job_id, checkpoint, "limit_reached")
            stopped = await self._enrich_page(job_id)
            if stopped:
                return stopped

    async def _drain_locations(
        self, job_id: str, job: dict, criteria: SearchCriteria, checkpoint: dict
    ) -> str:
        slots = slot_count(job)
        limit = criteria.candidate_limit

        while True:
            control = await self._control(job_id)
            if control:
                await self._finish_control(job_id, control, checkpoint)
                return control
            if slots >= limit:
                return await self._complete(job_id, checkpoint, "limit_reached")
            window = checkpoint.get("window")
            if not window:
                return await self._complete(job_id, checkpoint, "supply_exhausted")

            location = str(window["location"])
            if checkpoint.get("noted_index") != checkpoint.get("index"):
                await self._event(
                    job_id,
                    "search",
                    f"Searching {location}",
                    {
                        "location": location,
                        "start": window["start"],
                        "end": window["end"],
                    },
                )
                checkpoint["noted_index"] = checkpoint.get("index")
                await self._save_checkpoint(job_id, checkpoint)

            start = date.fromisoformat(window["start"])
            end = date.fromisoformat(window["end"])
            try:
                response = await self._fetch(window, criteria, start, end)
            except RateLimited as exc:
                control = await self._wait_through_rate_limit(job_id, checkpoint, exc)
                if control:
                    await self._finish_control(job_id, control, checkpoint)
                    return control
                continue

            await self._store_budget(job_id, response, retry_after_seconds=None)
            if response.status_code >= 400:
                await self._mark_incomplete(
                    job_id, f"github search returned {response.status_code}"
                )
                await self._event(
                    job_id,
                    "error",
                    f"GitHub search returned {response.status_code}",
                    {"location": location, "start": window["start"], "end": window["end"]},
                )
                _advance_location(checkpoint)
                await self._save_checkpoint(job_id, checkpoint)
                continue

            payload = response.json()
            if payload.get("incomplete_results"):
                tries = int(window.get("incomplete_tries") or 0) + 1
                window["incomplete_tries"] = tries
                checkpoint["window"] = window
                await self._save_checkpoint(job_id, checkpoint)
                if tries <= INCOMPLETE_RETRIES:
                    continue
                total = 0
            else:
                window["incomplete_tries"] = 0
                total = int(payload.get("total_count") or 0)
            capped = False
            if window.get("next_url") is None and total > SEARCH_RESULT_CAP:
                halves = split_date_window(start, end)
                if halves is not None:
                    left, right = halves
                    checkpoint["pending"] = [
                        {"start": right[0].isoformat(), "end": right[1].isoformat()},
                        *list(checkpoint.get("pending") or []),
                    ]
                    checkpoint["window"] = _window(*left, location)
                    await self._save_checkpoint(job_id, checkpoint)
                    await self._event(
                        job_id,
                        "split",
                        "Split a date window that exceeded 1000 results",
                        {
                            "location": location,
                            "total_count": total,
                            "start": window["start"],
                            "end": window["end"],
                        },
                    )
                    continue
                await self._mark_slice(
                    job_id, checkpoint, window, "single-day window exceeded 1000 results", total
                )
                capped = True

            tally = await self._tally_page(job_id, payload.get("items") or [])
            slots += tally.slots
            next_url = None if capped else parse_next_link(response.headers.get("link"))
            current_url = str(response.url)
            pages = int(window["pages_fetched"]) + 1
            if capped:
                _advance_location(checkpoint)
            elif next_url and next_url == current_url:
                await self._mark_slice(job_id, checkpoint, window, "repeated page", total)
                _advance_location(checkpoint)
            elif next_url and pages >= MAX_PAGES:
                await self._mark_slice(job_id, checkpoint, window, "pagination cap", total)
                _advance_location(checkpoint)
            elif next_url:
                window["next_url"] = next_url
                window["pages_fetched"] = pages
                checkpoint["window"] = window
            else:
                _advance_location(checkpoint)
            await self._flush_tally(job_id, tally, checkpoint)
            if slots >= limit:
                stopped = await self._enrich_page(job_id)
                if stopped:
                    return stopped
                return await self._complete(job_id, checkpoint, "limit_reached")
            stopped = await self._enrich_page(job_id)
            if stopped:
                return stopped

    async def _drain_allocated(
        self, job_id: str, job: dict, criteria: SearchCriteria, checkpoint: dict
    ) -> str:
        slots = slot_count(job)
        limit = criteria.candidate_limit
        stop_duplicates = int(((job.get("overlap_preview") or {}).get("fresh")) or 0) > 0

        while True:
            control = await self._control(job_id)
            if control:
                await self._finish_control(job_id, control, checkpoint)
                return control
            if slots >= limit:
                return await self._complete(job_id, checkpoint, "limit_reached")
            cursor = _select_cursor(checkpoint["cursors"], stop_duplicates=stop_duplicates)
            if cursor is None:
                if reallocate(checkpoint["cursors"]):
                    continue
                primary = sum(
                    taken(item) for item in checkpoint["cursors"] if item.get("wave") == "primary"
                )
                if primary <= 0 or not _open_overflow(checkpoint, limit, slots):
                    return await self._complete(job_id, checkpoint, "supply_exhausted")
                continue

            window = _window_from_cursor(cursor)
            checkpoint["window"] = window
            if not cursor.get("noted"):
                await self._event(
                    job_id,
                    "search",
                    f"Searching {cursor['location']}",
                    {
                        "location": cursor["location"],
                        "start": cursor["start"],
                        "end": cursor["end"],
                    },
                )
                cursor["noted"] = True
                await self._save_checkpoint(job_id, checkpoint)

            start = date.fromisoformat(cursor["start"])
            end = date.fromisoformat(cursor["end"])
            try:
                response = await self._fetch(window, criteria, start, end)
            except RateLimited as exc:
                control = await self._wait_through_rate_limit(job_id, checkpoint, exc)
                if control:
                    await self._finish_control(job_id, control, checkpoint)
                    return control
                continue

            await self._store_budget(job_id, response, retry_after_seconds=None)
            if response.status_code >= 400:
                reason = f"github search returned {response.status_code}"
                await self._mark_slice(job_id, checkpoint, window, reason, None)
                _advance_cursor(cursor)
                await self._save_checkpoint(job_id, checkpoint)
                continue

            payload = response.json()
            unknown = False
            if payload.get("incomplete_results"):
                tries = int(cursor.get("incomplete_tries") or 0) + 1
                cursor["incomplete_tries"] = tries
                await self._save_checkpoint(job_id, checkpoint)
                if tries <= INCOMPLETE_RETRIES:
                    continue
                cursor["total_unknown"] = True
                unknown = True
                total = 0
            else:
                cursor["incomplete_tries"] = 0
                total = int(payload.get("total_count") or 0)

            is_full = (
                cursor.get("next_url") is None
                and cursor["start"] == checkpoint["start"]
                and cursor["end"] == checkpoint["end"]
            )
            if not unknown:
                _remember_reported(cursor, total, full=is_full, unknown=False)
            if cursor.get("next_url") is None and not unknown and total == 0:
                _advance_cursor(cursor)
                await self._flush_tally(job_id, PageTally(), checkpoint)
                continue
            capped = False
            if cursor.get("next_url") is None and total > SEARCH_RESULT_CAP:
                halves = split_date_window(start, end)
                if halves is not None:
                    left, right = halves
                    cursor["pending"] = [
                        {"start": right[0].isoformat(), "end": right[1].isoformat()},
                        *list(cursor.get("pending") or []),
                    ]
                    cursor["start"], cursor["end"] = left[0].isoformat(), left[1].isoformat()
                    cursor["next_url"] = None
                    cursor["pages_fetched"] = 0
                    cursor["slice_fetched"] = 0
                    await self._save_checkpoint(job_id, checkpoint)
                    await self._event(
                        job_id,
                        "split",
                        "Split a date window that exceeded 1000 results",
                        {
                            "location": cursor["location"],
                            "total_count": total,
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                        },
                    )
                    continue
                await self._mark_slice(
                    job_id,
                    checkpoint,
                    window,
                    "single-day window exceeded 1000 results",
                    total,
                )
                capped = True

            tally = await self._tally_page(job_id, payload.get("items") or [])
            _apply_tally(cursor, tally)
            slots += tally.slots
            next_url = None if capped else parse_next_link(response.headers.get("link"))
            current_url = str(response.url)
            pages = int(cursor.get("pages_fetched") or 0) + 1
            if capped:
                _advance_cursor(cursor)
            elif next_url and next_url == current_url:
                await self._mark_slice(job_id, checkpoint, window, "repeated page", total)
                _advance_cursor(cursor)
            elif next_url and pages >= MAX_PAGES:
                await self._mark_slice(job_id, checkpoint, window, "pagination cap", total)
                _advance_cursor(cursor)
            elif next_url:
                cursor["next_url"] = next_url
                cursor["pages_fetched"] = pages
            else:
                _advance_cursor(cursor)
            await self._flush_tally(job_id, tally, checkpoint)
            if slots >= limit:
                stopped = await self._enrich_page(job_id)
                if stopped:
                    return stopped
                return await self._complete(job_id, checkpoint, "limit_reached")
            stopped = await self._enrich_page(job_id)
            if stopped:
                return stopped

    async def _tally_page(self, job_id: str, items: list) -> PageTally:
        tally = PageTally()
        for item in items:
            if not isinstance(item, dict) or "id" not in item or "login" not in item:
                continue
            outcome, source, moved = await upsert_candidate(self.db, job_id, item)
            tally.add(outcome, source, moved)
        return tally

    async def _flush_tally(self, job_id: str, tally: PageTally, checkpoint: dict) -> None:
        await self.db.search_jobs.update_one(
            {"_id": job_id},
            {
                "$set": {"checkpoint": checkpoint, "updated_at": utcnow()},
                "$inc": {
                    "counts.discovered": tally.inserted + tally.moved_in["discovered"],
                    "counts.emails": tally.moved_in["emails"],
                    "counts.linkedin_without_email": tally.moved_in["linkedin_without_email"],
                    "funnel.inserted": tally.inserted,
                    "funnel.updated": tally.updated,
                    "funnel.duplicate": tally.duplicate,
                    "funnel.fresh": tally.fresh,
                    "funnel.has_email": tally.has_email,
                    "funnel.moved_in": tally.updated,
                },
            },
        )
        for source_id, lost in tally.moved_out.items():
            if source_id == job_id or lost["discovered"] <= 0:
                continue
            inc = {"funnel.moved_out": lost["discovered"]}
            inc.update({f"counts.{key}": -value for key, value in lost.items() if value})
            await self.db.search_jobs.update_one({"_id": source_id}, {"$inc": inc})

    async def _mark_slice(
        self,
        job_id: str,
        checkpoint: dict,
        window: dict,
        reason: str,
        reported: int | None,
    ) -> None:
        await self._mark_incomplete(job_id, reason)
        checkpoint.setdefault("caps", []).append(
            {
                "location": window.get("location"),
                "start": window.get("start"),
                "end": window.get("end"),
                "reason": reason,
                "reported": reported,
                "fetched": int(window.get("pages_fetched") or 0),
            }
        )

    async def _enrich_page(self, job_id: str) -> str | None:
        """Fill profiles from the page just saved before the next search request."""

        outcome = await self.enrichment.enrich_pending(job_id)
        if outcome == "done":
            return None
        return outcome

    async def _fetch(self, window: dict, criteria: SearchCriteria, start: date, end: date):
        if window.get("next_url"):
            return await self.github.get(window["next_url"])
        query = build_user_query(criteria, start, end, str(window["location"]))
        return await self.github.get("/search/users", params={"q": query, "per_page": PAGE_SIZE})

    async def _control(self, job_id: str) -> str | None:
        job = await self.db.search_jobs.find_one({"_id": job_id}, {"control": 1})
        control = (job or {}).get("control")
        if control == "cancel":
            return "cancelled"
        if control == "pause":
            return "paused"
        return None

    async def _complete(self, job_id: str, checkpoint: dict, reason: str) -> str:
        checkpoint["windows"] = []
        checkpoint["phase"] = "discovery_done"
        if "window" in checkpoint:
            checkpoint["window"] = None
        await self.db.search_jobs.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "checkpoint": checkpoint,
                    "stop_reason": reason,
                    "updated_at": utcnow(),
                }
            },
        )
        return "done"

    async def _finish_control(self, job_id: str, status: str, checkpoint: dict) -> None:
        await self._save_checkpoint(job_id, checkpoint)
        await self.db.search_jobs.update_one(
            {"_id": job_id},
            {"$set": {"status": status, "control": None, "updated_at": utcnow()}},
        )
        await self._event(job_id, status, f"Job {status}", {})
        log.info(
            "job control applied",
            extra={"job_id": job_id, "status": status, "stage": "discovery"},
        )

    async def _wait_through_rate_limit(
        self, job_id: str, checkpoint: dict, exc: RateLimited
    ) -> str | None:
        """Sleep until the limit resets. Return a user pause or cancel, if one arrived."""

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
                "stage": "discovery",
                "retry_after_seconds": exc.retry_after,
                "reset_at": exc.reset_at,
                "remaining": exc.remaining,
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
