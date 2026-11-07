"""Job control routes."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.analysis import build_analysis, preview_overlap
from app.db import isoformat, utcnow
from app.discovery import new_job_document
from app.export import stream_job_csv
from app.hackernews_job import KIND as HACKERNEWS
from app.hackernews_job import new_hackernews_job
from app.models import HackerNewsCriteria, SearchCriteria
from app.schemas import AnalysisOut, JobList, JobOut

router = APIRouter(prefix="/api")

COUNT_KEYS = ("discovered", "enriched", "errors", "emails", "linkedin_without_email")


class JobCreate(BaseModel):
    criteria: SearchCriteria


class HackerNewsJobCreate(BaseModel):
    criteria: HackerNewsCriteria


def job_kind(document: dict) -> str:
    """``github`` for jobs written before ``kind`` existed."""

    kind = document.get("kind")
    return kind if kind == HACKERNEWS else "github"


def _budget(document: dict | None) -> dict:
    budget = document or {}
    return {
        "limit": budget.get("limit"),
        "remaining": budget.get("remaining"),
        "reset_at": isoformat(budget.get("reset_at")),
        "retry_after_seconds": budget.get("retry_after_seconds"),
    }


def public_job(document: dict) -> dict:
    """The job as the UI sees it. Counters come from the document; nothing else is read."""

    counts = document.get("counts") or {}
    lookups = document.get("github_lookups") or {}
    return {
        "id": document["_id"],
        "kind": job_kind(document),
        "status": document["status"],
        "criteria": document.get("criteria"),
        "hackernews_criteria": document.get("hackernews_criteria"),
        "threads": document.get("threads") or [],
        "github_lookups": {
            key: int(lookups.get(key) or 0) for key in ("done", "missing", "errors")
        },
        "incomplete": bool(document.get("incomplete")),
        "incomplete_reasons": document.get("incomplete_reasons") or [],
        "counts": {key: int(counts.get(key) or 0) for key in COUNT_KEYS},
        "request_budget": _budget(document.get("request_budget")),
        "error": document.get("error"),
        "overlap_preview": document.get("overlap_preview"),
        "created_at": isoformat(document.get("created_at")),
        "updated_at": isoformat(document.get("updated_at")),
    }


async def _load(db, job_id: str) -> dict:
    document = await db.search_jobs.find_one({"_id": job_id})
    if document is None:
        raise HTTPException(status_code=404, detail="job not found")
    return document


@router.get("/jobs", response_model=JobList)
async def list_jobs(request: Request) -> dict:
    cursor = request.app.state.db.search_jobs.find(
        {},
        {"status": 1, "created_at": 1, "kind": 1},
    ).sort([("created_at", -1)])
    documents = await cursor.to_list(100)
    return {
        "jobs": [
            {
                "id": document["_id"],
                "kind": job_kind(document),
                "status": document.get("status"),
                "created_at": isoformat(document.get("created_at")),
            }
            for document in documents
        ]
    }


@router.post("/jobs", status_code=201, response_model=JobOut)
async def create_job(body: JobCreate, request: Request) -> dict:
    db = request.app.state.db
    document = new_job_document(body.criteria)
    document["overlap_preview"] = await preview_overlap(db, body.criteria, document["created_at"])
    await db.search_jobs.insert_one(document)
    return public_job(document)


@router.post("/jobs/hackernews", status_code=201, response_model=JobOut)
async def create_hackernews_job(body: HackerNewsJobCreate, request: Request) -> dict:
    """Queue a Hacker News search. Start it with ``POST /jobs/{id}/start`` as usual."""

    document = new_hackernews_job(body.criteria)
    await request.app.state.db.search_jobs.insert_one(document)
    return public_job(document)


@router.get("/jobs/{job_id}/analysis", response_model=AnalysisOut)
async def job_analysis(job_id: str, request: Request) -> dict:
    db = request.app.state.db
    document = await _load(db, job_id)
    if job_kind(document) == HACKERNEWS:
        raise HTTPException(status_code=404, detail="analysis is only for GitHub searches")
    return await build_analysis(db, document)


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: str, request: Request) -> dict:
    db = request.app.state.db
    return public_job(await _load(db, job_id))


@router.get("/jobs/{job_id}/export.csv")
async def export_job_csv(
    job_id: str,
    request: Request,
    include_no_contact: bool = Query(default=True),
) -> StreamingResponse:
    await _load(request.app.state.db, job_id)
    filename = f'attachment; filename="fetchiq-{job_id}.csv"'
    return StreamingResponse(
        stream_job_csv(
            request.app.state.db,
            job_id,
            include_no_contact=include_no_contact,
        ),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": filename},
    )


@router.post("/jobs/{job_id}/start", response_model=JobOut)
async def start_job(job_id: str, request: Request) -> dict:
    return await _kick(job_id, request, allowed={"queued", "paused", "incomplete"})


@router.post("/jobs/{job_id}/resume", response_model=JobOut)
async def resume_job(job_id: str, request: Request) -> dict:
    return await _kick(job_id, request, allowed={"paused", "incomplete"})


@router.post("/jobs/{job_id}/pause", response_model=JobOut)
async def pause_job(job_id: str, request: Request) -> dict:
    db = request.app.state.db
    document = await _load(db, job_id)
    if document["status"] == "paused":
        return public_job(document)
    if document["status"] not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="job cannot be paused")
    if document["status"] == "queued":
        await db.search_jobs.update_one(
            {"_id": job_id},
            {"$set": {"status": "paused", "control": None, "updated_at": utcnow()}},
        )
    else:
        await db.search_jobs.update_one(
            {"_id": job_id},
            {"$set": {"control": "pause", "updated_at": utcnow()}},
        )
    return public_job(await _load(db, job_id))


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str, request: Request) -> dict:
    db = request.app.state.db
    document = await _load(db, job_id)
    if document["status"] in {"succeeded", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="job cannot be cancelled")
    if document["status"] == "running":
        await db.search_jobs.update_one(
            {"_id": job_id},
            {"$set": {"control": "cancel", "updated_at": utcnow()}},
        )
    else:
        await db.search_jobs.update_one(
            {"_id": job_id},
            {"$set": {"status": "cancelled", "control": None, "updated_at": utcnow()}},
        )
    return public_job(await _load(db, job_id))


async def running_hackernews_conflict(db, document: dict) -> str | None:
    """One Hacker News job at a time: two would read and store the same posts."""

    other = await db.search_jobs.find_one(
        {"_id": {"$ne": document["_id"]}, "status": "running", "kind": HACKERNEWS},
        {"_id": 1},
    )
    if other is None:
        return None
    return (
        f"Job {other['_id']} is already reading Hacker News. "
        "Wait for it to finish, or pause or cancel it, before starting this one."
    )


async def running_country_conflict(db, document: dict) -> str | None:
    """Explain why this job must wait: another running job already searches a country of its own.

    Two jobs on one country read the same GitHub pages and take each other's unchecked
    profiles, so the second one is held until the first finishes, pauses or is cancelled.
    """

    if job_kind(document) == HACKERNEWS:
        return await running_hackernews_conflict(db, document)
    countries = [
        country
        for country in ((document.get("criteria") or {}).get("countries") or [])
        if isinstance(country, str)
    ]
    if not countries:
        return None
    other = await db.search_jobs.find_one(
        {
            "_id": {"$ne": document["_id"]},
            "status": "running",
            "criteria.countries": {"$in": countries},
        },
        {"criteria.countries": 1},
    )
    if other is None:
        return None
    shared = [
        country
        for country in countries
        if country in ((other.get("criteria") or {}).get("countries") or [])
    ]
    return (
        f"Job {other['_id']} is already searching {', '.join(shared)}. "
        "Wait for it to finish, or pause or cancel it, before starting this one."
    )


async def _kick(job_id: str, request: Request, *, allowed: set[str]) -> dict:
    db = request.app.state.db
    document = await _load(db, job_id)
    held = document["status"] == "failed" and str(document.get("error") or "").startswith(
        "Could not resolve"
    )
    if document["status"] not in allowed and not held:
        raise HTTPException(status_code=409, detail="job cannot be started")
    conflict = await running_country_conflict(db, document)
    if conflict is not None:
        raise HTTPException(status_code=409, detail=conflict)
    try:
        await request.app.state.runner.kick(job_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return public_job(await _load(db, job_id))
