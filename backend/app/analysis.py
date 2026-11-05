"""Explain why a search kept fewer profiles than its limit.

The report reads the raw job document. The public job payload replaces the
insert counter with the live candidate count, which falls when a later search
moves profiles away.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.dates import account_age_window
from app.db import isoformat
from app.geo import COUNTRIES
from app.models import SearchCriteria

FRESH_DAYS = 14


def _age(document: dict[str, Any]) -> dict[str, int]:
    age = ((document.get("criteria") or {}).get("account_age")) or {}
    return {
        "min_years": int(age.get("min_years") or 0),
        "min_months": int(age.get("min_months") or 0),
        "max_years": int(age.get("max_years") or 0),
        "max_months": int(age.get("max_months") or 0),
    }


def _created_day(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def job_window(document: dict[str, Any]) -> tuple[date, date] | None:
    """Created-date window, using the checkpoint dates or the job's own creation date."""

    checkpoint = document.get("checkpoint") or {}
    start = checkpoint.get("start")
    end = checkpoint.get("end")
    if isinstance(start, str) and isinstance(end, str):
        return date.fromisoformat(start), date.fromisoformat(end)
    today = _created_day(document.get("created_at"))
    if today is None:
        return None
    age = _age(document)
    return account_age_window(today=today, **age)


def windows_overlap(left: tuple[date, date], right: tuple[date, date]) -> bool:
    return left[0] <= right[1] and right[0] <= left[1]


def slot_count(document: dict[str, Any]) -> int:
    funnel = document.get("funnel") or {}
    if "inserted" in funnel or "updated" in funnel:
        return int(funnel.get("inserted") or 0) + int(funnel.get("updated") or 0)
    return int((document.get("counts") or {}).get("discovered") or 0)


def stop_reason(document: dict[str, Any]) -> str:
    stored = document.get("stop_reason")
    if stored in {"limit_reached", "supply_exhausted", "stopped_early"}:
        return str(stored)
    status = document.get("status")
    if status in {"cancelled", "paused", "failed"}:
        return str(status)
    error = str(document.get("error") or "")
    phase = (document.get("checkpoint") or {}).get("phase")
    if status == "incomplete" and error.startswith("Could not resolve") and phase == "discovery":
        return "stopped_early"
    limit = int(((document.get("criteria") or {}).get("candidate_limit")) or 0)
    if slot_count(document) >= limit > 0:
        return "limit_reached"
    if phase in {"discovery_done", "enrichment", "enrichment_done"}:
        return "supply_exhausted"
    if status == "incomplete":
        return "stopped_early"
    return "running"


def _countries(document: dict[str, Any]) -> list[str]:
    raw = (document.get("criteria") or {}).get("countries") or []
    return [country for country in raw if isinstance(country, str)]


async def overlapping_jobs(db, document: dict[str, Any]) -> list[dict[str, Any]]:
    """Earlier jobs whose countries and created-date windows meet this search."""

    window = job_window(document)
    countries = set(_countries(document))
    if window is None or not countries:
        return []
    created = document.get("created_at")
    query: dict[str, Any] = {"criteria.countries": {"$in": list(countries)}}
    if isinstance(created, datetime):
        query["created_at"] = {"$lt": created}
    cursor = db.search_jobs.find(
        query,
        {
            "criteria": 1,
            "created_at": 1,
            "checkpoint.start": 1,
            "checkpoint.end": 1,
        },
    )
    chosen: list[dict[str, Any]] = []
    for earlier in await cursor.to_list(None):
        if earlier.get("_id") == document.get("_id"):
            continue
        if not countries.intersection(_countries(earlier)):
            continue
        other = job_window(earlier)
        if other is not None and windows_overlap(window, other):
            chosen.append(earlier)
    return chosen


def _aware(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def _overlap_rows(
    db,
    document: dict[str, Any],
    earlier: list[dict[str, Any]],
    *,
    fresh_cutoff: datetime | None,
) -> list[dict[str, Any]]:
    if not earlier:
        return []
    countries = set(_countries(document))
    ids = [item["_id"] for item in earlier]
    found = await db.candidates.find(
        {"job_id": {"$in": ids}},
        {"job_id": 1, "country": 1, "last_checked_at": 1},
    ).to_list(None)
    stats: dict[str, dict[str, int]] = {}
    cutoff = _aware(fresh_cutoff)
    for candidate in found:
        job_id = candidate.get("job_id")
        if not isinstance(job_id, str):
            continue
        bucket = stats.setdefault(job_id, {"live": 0, "matched": 0, "fresh": 0})
        bucket["live"] += 1
        if candidate.get("country") not in countries:
            continue
        bucket["matched"] += 1
        checked = _aware(candidate.get("last_checked_at"))
        if cutoff is not None and checked is not None and checked >= cutoff:
            bucket["fresh"] += 1
    rows: list[dict[str, Any]] = []
    for item in earlier:
        row = stats.get(item["_id"]) or {"live": 0, "matched": 0, "fresh": 0}
        rows.append(
            {
                "id": item["_id"],
                "countries": _countries(item),
                "live": row["live"],
                "matched": row["matched"],
                "fresh": row["fresh"],
            }
        )
    rows.sort(key=lambda row: row["matched"], reverse=True)
    return rows


async def preview_overlap(db, criteria: SearchCriteria, created_at: datetime) -> dict[str, Any]:
    """Snapshot who is already saved, computed once when the job is created."""

    probe = {
        "_id": None,
        "criteria": criteria.model_dump(),
        "created_at": created_at,
        "checkpoint": None,
    }
    earlier = await overlapping_jobs(db, probe)
    rows = await _overlap_rows(
        db,
        probe,
        earlier,
        fresh_cutoff=created_at - timedelta(days=FRESH_DAYS),
    )
    return {"fresh": sum(row["fresh"] for row in rows), "jobs": rows}


def _full_window_totals(document: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, int]:
    window = job_window(document)
    if window is None:
        return {}
    start, end = window[0].isoformat(), window[1].isoformat()
    totals: dict[str, int] = {}
    for event in events:
        detail = event.get("detail") or {}
        location = detail.get("location")
        if not isinstance(location, str) or location in totals:
            continue
        if detail.get("start") == start and detail.get("end") == end:
            totals[location] = int(detail.get("total_count") or 0)
    return totals


def _cursor_places(checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
    places: list[dict[str, Any]] = []
    for cursor in checkpoint.get("cursors") or []:
        duplicates = (
            int(cursor.get("duplicate") or 0)
            + int(cursor.get("fresh") or 0)
            + int(cursor.get("has_email") or 0)
        )
        if cursor.get("exhausted"):
            status = "exhausted"
        elif cursor.get("caps"):
            status = "capped"
        elif taken_slots(cursor) >= int(cursor.get("share") or 0) > 0:
            status = "limit_filled"
        else:
            status = "unread"
        places.append(
            {
                "location": cursor.get("location"),
                "reported": cursor.get("reported"),
                "fetched": int(cursor.get("fetched") or 0),
                "inserted": int(cursor.get("inserted") or 0),
                "updated": int(cursor.get("updated") or 0),
                "duplicates": duplicates,
                "status": status,
            }
        )
    return places


def taken_slots(cursor: dict[str, Any]) -> int:
    return int(cursor.get("inserted") or 0) + int(cursor.get("updated") or 0)


def _event_places(
    document: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    totals = _full_window_totals(document, events)
    locations = list((document.get("checkpoint") or {}).get("locations") or [])
    unknown = 0
    places: list[dict[str, Any]] = []
    seen = set(locations)
    for location in locations:
        reported = totals.get(location)
        if reported is None:
            unknown += 1
        places.append(
            {
                "location": location,
                "reported": reported,
                "fetched": None,
                "inserted": None,
                "updated": None,
                "duplicates": None,
                "status": "unknown" if reported is None else "reported",
            }
        )
    for location, reported in totals.items():
        if location in seen:
            continue
        places.append(
            {
                "location": location,
                "reported": reported,
                "fetched": None,
                "inserted": None,
                "updated": None,
                "duplicates": None,
                "status": "reported",
            }
        )
    return places, unknown


def _suggestions(
    document: dict[str, Any],
    reason: str,
    places: list[dict[str, Any]],
    saved: list[dict[str, Any]],
    caps: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    window = job_window(document)
    covered: list[str] = []
    for row in saved:
        for country in row["countries"]:
            if country not in covered:
                covered.append(country)
    for country in _countries(document):
        if country not in covered:
            covered.append(country)
    uncollected = [country for country in COUNTRIES if country not in covered]
    lines: list[str] = []
    if reason == "supply_exhausted":
        known = [
            f"{place['location']} {place['reported']}"
            for place in places
            if isinstance(place.get("reported"), int)
        ]
        if known:
            lines.append(
                "GitHub's full-window totals for the location strings that were recorded: "
                + ", ".join(known)
                + ". The same person can match more than one of those strings."
            )
        else:
            lines.append(
                "Every location this search was allowed to read was finished or capped "
                "before the limit filled."
            )
    for row in saved:
        if row["live"] or row["matched"]:
            lines.append(
                f"Job {row['id']} still holds {row['live']} profiles, "
                f"{row['matched']} of them resolved to a country in this search."
            )
    if window is not None:
        lines.append(
            f"This search covers accounts created {window[0].isoformat()} "
            f"through {window[1].isoformat()}. A wider account age queries the dates outside it."
        )
    if caps:
        for cap in caps:
            lines.append(
                f"{cap['location']} on {cap['start']} through {cap['end']} "
                f"stopped for {cap['reason']}."
            )
    if uncollected:
        lines.append("Countries with no overlapping search in this window are listed separately.")
    return lines, covered, uncollected


def _headline(document: dict[str, Any], reason: str, live: int, inserted: int) -> str:
    limit = int(((document.get("criteria") or {}).get("candidate_limit")) or 0)
    if reason == "limit_reached":
        return f"The limit of {limit} filled. {live} profiles are still on this job."
    if reason == "supply_exhausted":
        return (
            f"{live} profiles are still on this job and {inserted} were new inserts. "
            f"The locations ran out before the limit of {limit}."
        )
    if reason == "stopped_early":
        return "The search stopped before the location list finished."
    if reason == "paused":
        return "The search is paused."
    if reason == "cancelled":
        return "The search was cancelled."
    if reason == "failed":
        return "The search failed."
    return "The search is still running."


async def build_analysis(db, document: dict[str, Any]) -> dict[str, Any]:
    reason = stop_reason(document)
    live = await db.candidates.count_documents({"job_id": document["_id"]})
    funnel = document.get("funnel") or {}
    has_funnel = "inserted" in funnel or "updated" in funnel
    inserted = (
        int(funnel["inserted"])
        if has_funnel
        else int((document.get("counts") or {}).get("discovered") or 0)
    )
    updated = int(funnel.get("updated") or 0) if has_funnel else None
    duplicates = None
    if has_funnel:
        duplicates = (
            int(funnel.get("duplicate") or 0)
            + int(funnel.get("fresh") or 0)
            + int(funnel.get("has_email") or 0)
        )
    slots = inserted + (updated or 0)
    limit = int(((document.get("criteria") or {}).get("candidate_limit")) or 0)
    shortfall = max(limit - slots, 0) if reason == "supply_exhausted" else 0
    if has_funnel:
        moved_out = max(inserted + int(funnel.get("moved_in") or 0) - int(live), 0)
    else:
        moved_out = None

    checkpoint = document.get("checkpoint") or {}
    caps = list(checkpoint.get("caps") or [])
    events = await db.job_events.find({"job_id": document["_id"], "kind": "split"}).to_list(None)
    if checkpoint.get("cursors"):
        places = _cursor_places(checkpoint)
        unknown = sum(1 for place in places if place.get("reported") is None)
    else:
        places, unknown = _event_places(document, events)
    created = document.get("created_at")
    cutoff = created - timedelta(days=FRESH_DAYS) if isinstance(created, datetime) else None
    earlier = await overlapping_jobs(db, document)
    saved = await _overlap_rows(db, document, earlier, fresh_cutoff=cutoff)
    suggestions, covered, uncollected = _suggestions(document, reason, places, saved, caps)
    window = job_window(document)
    return {
        "stop_reason": reason,
        "headline": _headline(document, reason, int(live), inserted),
        "limit": limit,
        "live": int(live),
        "inserted": inserted,
        "updated": updated,
        "duplicates": duplicates,
        "slots": slots,
        "shortfall": shortfall,
        "moved_out": moved_out,
        "window": (
            {"start": window[0].isoformat(), "end": window[1].isoformat()}
            if window is not None
            else None
        ),
        "places_known": sum(1 for place in places if place.get("reported") is not None),
        "places_unknown": unknown,
        "places": places,
        "caps": caps,
        "already_saved": saved,
        "covered_countries": covered,
        "uncollected_countries": uncollected,
        "suggestions": suggestions,
        "created_at": isoformat(created),
    }
