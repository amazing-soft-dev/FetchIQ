"""Job control routes."""

from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.db import utcnow
from app.discovery import new_job_document
from app.export import stream_job_csv
from app.models import SearchCriteria

router = APIRouter(prefix="/api")


class JobCreate(BaseModel):
    criteria: SearchCriteria


def _iso(value) -> str | None:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return None


def _budget(document: dict | None) -> dict:
    budget = document or {}
    return {
        "limit": budget.get("limit"),
        "remaining": budget.get("remaining"),
        "reset_at": _iso(budget.get("reset_at")),
        "retry_after_seconds": budget.get("retry_after_seconds"),
    }


async def progress_counts(db, job_id: str) -> dict[str, int]:
    """Profiles stored on this job, not the historical insert counter."""

    discovered = await db.candidates.count_documents({"job_id": job_id})
    enriched = await db.candidates.count_documents(
        {"job_id": job_id, "enrichment_status": "enriched"}
    )
    return {"discovered": int(discovered), "enriched": int(enriched)}


async def contact_counts(db, job_id: str) -> dict[str, int]:
    """Count stored emails and usernames that have LinkedIn but no email."""

    candidates = await db.candidates.find(
        {"job_id": job_id},
        {"login": 1, "github_user_id": 1},
    ).to_list(None)
    user_ids: list[int] = []
    seen_ids: set[int] = set()
    for candidate in candidates:
        user_id = candidate.get("github_user_id")
        if isinstance(user_id, int) and user_id not in seen_ids:
            seen_ids.add(user_id)
            user_ids.append(user_id)
    if not user_ids:
        return {"emails": 0, "linkedin_without_email": 0}

    evidence = await db.contact_evidence.find(
        {"github_user_id": {"$in": user_ids}, "kind": {"$in": ["email", "linkedin"]}},
        {"github_user_id": 1, "kind": 1},
    ).to_list(None)
    emails = 0
    kinds_by_user: dict[int, set[str]] = {}
    for item in evidence:
        user_id = item.get("github_user_id")
        kind = item.get("kind")
        if not isinstance(user_id, int) or kind not in {"email", "linkedin"}:
            continue
        kinds_by_user.setdefault(user_id, set()).add(kind)
        if kind == "email":
            emails += 1

    linkedin_without_email = 0
    seen_logins: set[str] = set()
    for candidate in candidates:
        login = candidate.get("login")
        if not isinstance(login, str) or not login or login in seen_logins:
            continue
        seen_logins.add(login)
        kinds = kinds_by_user.get(candidate.get("github_user_id"), set())
        if "linkedin" in kinds and "email" not in kinds:
            linkedin_without_email += 1
    return {"emails": emails, "linkedin_without_email": linkedin_without_email}


def public_job(
    document: dict,
    contact: dict | None = None,
    progress: dict | None = None,
) -> dict:
    counts = document.get("counts") or {}
    report = contact or {"emails": 0, "linkedin_without_email": 0}
    live = progress or {}
    return {
        "id": document["_id"],
        "status": document["status"],
        "criteria": document["criteria"],
        "incomplete": bool(document.get("incomplete")),
        "incomplete_reasons": document.get("incomplete_reasons") or [],
        "counts": {
            "discovered": int(live.get("discovered", counts.get("discovered") or 0)),
            "enriched": int(live.get("enriched", counts.get("enriched") or 0)),
            "errors": int(counts.get("errors") or 0),
            "emails": int(report.get("emails") or 0),
            "linkedin_without_email": int(report.get("linkedin_without_email") or 0),
        },
        "request_budget": _budget(document.get("request_budget")),
        "error": document.get("error"),
        "created_at": _iso(document.get("created_at")),
        "updated_at": _iso(document.get("updated_at")),
    }


async def present_job(db, document: dict) -> dict:
    job_id = document["_id"]
    return public_job(
        document,
        await contact_counts(db, job_id),
        await progress_counts(db, job_id),
    )


async def _load(db, job_id: str) -> dict:
    document = await db.search_jobs.find_one({"_id": job_id})
    if document is None:
        raise HTTPException(status_code=404, detail="job not found")
    return document


@router.get("/jobs")
async def list_jobs(request: Request) -> dict:
    cursor = request.app.state.db.search_jobs.find(
        {},
        {"status": 1, "created_at": 1},
    ).sort([("created_at", -1)])
    documents = await cursor.to_list(100)
    return {
        "jobs": [
            {
                "id": document["_id"],
                "status": document.get("status"),
                "created_at": _iso(document.get("created_at")),
            }
            for document in documents
        ]
    }


@router.post("/jobs", status_code=201)
async def create_job(body: JobCreate, request: Request) -> dict:
    document = new_job_document(body.criteria)
    await request.app.state.db.search_jobs.insert_one(document)
    return await present_job(request.app.state.db, document)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict:
    db = request.app.state.db
    return await present_job(db, await _load(db, job_id))


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


@router.post("/jobs/{job_id}/start")
async def start_job(job_id: str, request: Request) -> dict:
    return await _kick(job_id, request, allowed={"queued", "paused", "incomplete"})


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str, request: Request) -> dict:
    return await _kick(job_id, request, allowed={"paused", "incomplete"})


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str, request: Request) -> dict:
    db = request.app.state.db
    document = await _load(db, job_id)
    if document["status"] == "paused":
        return await present_job(db, document)
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
    return await present_job(db, await _load(db, job_id))


@router.post("/jobs/{job_id}/cancel")
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
    return await present_job(db, await _load(db, job_id))


async def _kick(job_id: str, request: Request, *, allowed: set[str]) -> dict:
    db = request.app.state.db
    document = await _load(db, job_id)
    held = document["status"] == "failed" and str(document.get("error") or "").startswith(
        "Could not resolve"
    )
    if document["status"] not in allowed and not held:
        raise HTTPException(status_code=409, detail="job cannot be started")
    try:
        await request.app.state.runner.kick(job_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await present_job(db, await _load(db, job_id))
