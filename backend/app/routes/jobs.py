"""Job control routes."""

from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.db import utcnow
from app.discovery import new_job_document
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


def public_job(document: dict) -> dict:
    return {
        "id": document["_id"],
        "status": document["status"],
        "criteria": document["criteria"],
        "incomplete": bool(document.get("incomplete")),
        "incomplete_reasons": document.get("incomplete_reasons") or [],
        "counts": document.get("counts") or {"discovered": 0, "enriched": 0, "errors": 0},
        "request_budget": _budget(document.get("request_budget")),
        "error": document.get("error"),
        "created_at": _iso(document.get("created_at")),
        "updated_at": _iso(document.get("updated_at")),
    }


async def _load(db, job_id: str) -> dict:
    document = await db.search_jobs.find_one({"_id": job_id})
    if document is None:
        raise HTTPException(status_code=404, detail="job not found")
    return document


@router.post("/jobs", status_code=201)
async def create_job(body: JobCreate, request: Request) -> dict:
    document = new_job_document(body.criteria)
    await request.app.state.db.search_jobs.insert_one(document)
    return public_job(document)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict:
    return public_job(await _load(request.app.state.db, job_id))


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
    return public_job(await _load(db, job_id))


async def _kick(job_id: str, request: Request, *, allowed: set[str]) -> dict:
    db = request.app.state.db
    document = await _load(db, job_id)
    if document["status"] not in allowed:
        raise HTTPException(status_code=409, detail="job cannot be started")
    try:
        await request.app.state.runner.kick(job_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return public_job(await _load(db, job_id))
