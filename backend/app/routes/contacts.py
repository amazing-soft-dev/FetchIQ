"""Review stored contacts, their outreach status, and a filtered CSV."""

import re
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.contacts import (
    NOT_SET,
    PAGE_SIZES,
    contact_facts,
    matches_contact_filters,
    status_slug,
)
from app.db import utcnow
from app.export import COLUMNS, build_row, format_csv_row

router = APIRouter(prefix="/api")

_STATUS_QUERY = Query(default=[])
_EMAIL = frozenset({"any", "has", "missing"})
_KIND = frozenset({"any", "personal", "other"})
_LINKEDIN = frozenset({"any", "has", "missing"})


class StatusCreate(BaseModel):
    label: str = Field(min_length=1, max_length=40)


class StatusRename(BaseModel):
    label: str = Field(min_length=1, max_length=40)


class StatusUpdate(BaseModel):
    status: str | None = None


class BulkStatus(BaseModel):
    logins: list[str] = Field(min_length=1, max_length=10000)
    status: str | None = None


class ContactExport(BaseModel):
    logins: list[str] = Field(min_length=1, max_length=10000)


def _iso(value) -> str | None:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return None


def _clean_label(label: str) -> str:
    cleaned = " ".join(label.split())
    if not cleaned:
        raise HTTPException(status_code=422, detail="status label is required")
    return cleaned


async def _statuses(db) -> list[dict]:
    cursor = db.outreach_statuses.find({}).sort([("position", 1), ("label", 1)])
    return await cursor.to_list(None)


def _public_status(document: dict) -> dict:
    return {
        "id": document["_id"],
        "label": document["label"],
        "position": document.get("position") or 0,
    }


async def _require_status(db, status_id: str) -> dict:
    document = await db.outreach_statuses.find_one({"_id": status_id})
    if document is None:
        raise HTTPException(status_code=404, detail="status not found")
    return document


async def _label_taken(db, label: str, *, ignore_id: str | None = None) -> bool:
    wanted = label.casefold()
    for document in await _statuses(db):
        if ignore_id is not None and document["_id"] == ignore_id:
            continue
        if str(document.get("label") or "").casefold() == wanted:
            return True
    return False


def _candidate_query(
    *,
    q: str,
    job_id: str,
    statuses: list[str],
) -> dict:
    parts: list[dict] = []
    if job_id:
        parts.append({"job_id": job_id})
    text = q.strip()
    if text:
        pattern = re.escape(text)
        parts.append(
            {
                "$or": [
                    {"login": {"$regex": pattern, "$options": "i"}},
                    {"name": {"$regex": pattern, "$options": "i"}},
                    {"public_email": {"$regex": pattern, "$options": "i"}},
                ]
            }
        )
    if statuses:
        clauses: list[dict] = []
        ids = [item for item in statuses if item != NOT_SET]
        if ids:
            clauses.append({"outreach_status": {"$in": ids}})
        if NOT_SET in statuses:
            clauses.append(
                {
                    "$or": [
                        {"outreach_status": None},
                        {"outreach_status": ""},
                        {"outreach_status": {"$exists": False}},
                    ]
                }
            )
        if len(clauses) == 1:
            parts.append(clauses[0])
        elif clauses:
            parts.append({"$or": clauses})
    if not parts:
        return {}
    if len(parts) == 1:
        return parts[0]
    return {"$and": parts}


def _needs_evidence(email: str, email_kind: str, linkedin: str) -> bool:
    return email != "any" or email_kind != "any" or linkedin != "any"


async def _evidence_for(db, user_ids: list[int]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {user_id: [] for user_id in user_ids}
    if not user_ids:
        return grouped
    cursor = db.contact_evidence.find({"github_user_id": {"$in": user_ids}})
    for item in await cursor.to_list(None):
        user_id = item.get("github_user_id")
        if isinstance(user_id, int):
            grouped.setdefault(user_id, []).append(item)
    return grouped


def _present(candidate: dict, evidence: list[dict]) -> dict:
    facts = contact_facts(candidate, evidence)
    kinds: list[str] = []
    if facts["personal"]:
        kinds.append("personal")
    if facts["other"]:
        kinds.append("other")
    status = candidate.get("outreach_status") or None
    return {
        "login": candidate.get("login") or "",
        "name": candidate.get("name"),
        "profile_url": candidate.get("profile_url") or "",
        "public_email": facts["public_email"],
        "email_kinds": kinds,
        "linkedin_url": facts["linkedin_url"],
        "country": candidate.get("country"),
        "outreach_status": status,
        "last_checked_at": _iso(candidate.get("last_checked_at")),
        "job_id": candidate.get("job_id"),
    }


async def _sorted_candidates(db, query: dict) -> list[dict]:
    cursor = db.candidates.find(query).sort([("last_checked_at", -1), ("login", 1)])
    return await cursor.to_list(None)


async def _filtered_candidates(
    db,
    query: dict,
    *,
    email: str,
    email_kind: str,
    linkedin: str,
) -> list[tuple[dict, list[dict]]]:
    candidates = await _sorted_candidates(db, query)
    user_ids = [
        candidate["github_user_id"]
        for candidate in candidates
        if isinstance(candidate.get("github_user_id"), int)
    ]
    grouped = await _evidence_for(db, user_ids)
    chosen: list[tuple[dict, list[dict]]] = []
    for candidate in candidates:
        user_id = candidate.get("github_user_id")
        evidence = grouped.get(user_id, []) if isinstance(user_id, int) else []
        facts = contact_facts(candidate, evidence)
        if matches_contact_filters(facts, email=email, email_kind=email_kind, linkedin=linkedin):
            chosen.append((candidate, evidence))
    return chosen


def _check_filters(email: str, email_kind: str, linkedin: str, page: int, page_size: int) -> None:
    if email not in _EMAIL or email_kind not in _KIND or linkedin not in _LINKEDIN:
        raise HTTPException(status_code=422, detail="unknown contact filter")
    if page < 1:
        raise HTTPException(status_code=422, detail="page must be at least 1")
    if page_size not in PAGE_SIZES:
        raise HTTPException(status_code=422, detail="page size must be 25, 50, or 100")


@router.get("/outreach-statuses")
async def list_statuses(request: Request) -> dict:
    documents = await _statuses(request.app.state.db)
    return {"statuses": [_public_status(document) for document in documents]}


@router.post("/outreach-statuses", status_code=201)
async def create_status(body: StatusCreate, request: Request) -> dict:
    db = request.app.state.db
    label = _clean_label(body.label)
    slug = status_slug(label)
    if not slug or slug == NOT_SET:
        raise HTTPException(status_code=422, detail="status label is required")
    if await db.outreach_statuses.find_one({"_id": slug}) is not None or await _label_taken(
        db, label
    ):
        raise HTTPException(status_code=409, detail="status already exists")
    documents = await _statuses(db)
    position = 1
    if documents:
        position = max(document.get("position") or 0 for document in documents) + 1
    document = {"_id": slug, "label": label, "position": position}
    await db.outreach_statuses.insert_one(document)
    return _public_status(document)


@router.patch("/outreach-statuses/{status_id}")
async def rename_status(status_id: str, body: StatusRename, request: Request) -> dict:
    db = request.app.state.db
    document = await _require_status(db, status_id)
    label = _clean_label(body.label)
    if await _label_taken(db, label, ignore_id=status_id):
        raise HTTPException(status_code=409, detail="status already exists")
    await db.outreach_statuses.update_one({"_id": status_id}, {"$set": {"label": label}})
    document["label"] = label
    return _public_status(document)


@router.delete("/outreach-statuses/{status_id}")
async def delete_status(status_id: str, request: Request) -> dict:
    db = request.app.state.db
    await _require_status(db, status_id)
    await db.candidates.update_many(
        {"outreach_status": status_id},
        {"$unset": {"outreach_status": ""}, "$set": {"updated_at": utcnow()}},
    )
    await db.outreach_statuses.delete_one({"_id": status_id})
    return {"deleted": status_id}


@router.get("/contacts")
async def list_contacts(
    request: Request,
    page: int = Query(default=1),
    page_size: int = Query(default=25),
    email: str = Query(default="any"),
    email_kind: str = Query(default="any"),
    linkedin: str = Query(default="any"),
    status: list[str] = _STATUS_QUERY,
    q: str = Query(default=""),
    job_id: str = Query(default=""),
) -> dict:
    _check_filters(email, email_kind, linkedin, page, page_size)
    db = request.app.state.db
    query = _candidate_query(q=q, job_id=job_id, statuses=status)
    skip = (page - 1) * page_size
    if _needs_evidence(email, email_kind, linkedin):
        matched = await _filtered_candidates(
            db, query, email=email, email_kind=email_kind, linkedin=linkedin
        )
        total = len(matched)
        page_rows = matched[skip : skip + page_size]
        contacts = [_present(candidate, evidence) for candidate, evidence in page_rows]
    else:
        total = await db.candidates.count_documents(query)
        cursor = (
            db.candidates.find(query)
            .sort([("last_checked_at", -1), ("login", 1)])
            .skip(skip)
            .limit(page_size)
        )
        candidates = await cursor.to_list(page_size)
        user_ids = [
            candidate["github_user_id"]
            for candidate in candidates
            if isinstance(candidate.get("github_user_id"), int)
        ]
        grouped = await _evidence_for(db, user_ids)
        contacts = [
            _present(
                candidate,
                grouped.get(candidate.get("github_user_id"), [])
                if isinstance(candidate.get("github_user_id"), int)
                else [],
            )
            for candidate in candidates
        ]
    return {"contacts": contacts, "total": total, "page": page, "page_size": page_size}


@router.get("/contacts/logins")
async def list_contact_logins(
    request: Request,
    email: str = Query(default="any"),
    email_kind: str = Query(default="any"),
    linkedin: str = Query(default="any"),
    status: list[str] = _STATUS_QUERY,
    q: str = Query(default=""),
    job_id: str = Query(default=""),
) -> dict:
    _check_filters(email, email_kind, linkedin, 1, 25)
    db = request.app.state.db
    query = _candidate_query(q=q, job_id=job_id, statuses=status)
    matched = await _filtered_candidates(
        db, query, email=email, email_kind=email_kind, linkedin=linkedin
    )
    logins = [candidate.get("login") or "" for candidate, _evidence in matched]
    logins = [login for login in logins if login]
    return {"logins": logins, "total": len(logins)}


async def _apply_status(db, logins: list[str], status: str | None) -> int:
    cleaned = [login for login in logins if login]
    if not cleaned:
        raise HTTPException(status_code=422, detail="select at least one contact")
    if status is not None:
        await _require_status(db, status)
    now = utcnow()
    if status is None:
        result = await db.candidates.update_many(
            {"login": {"$in": cleaned}},
            {"$unset": {"outreach_status": ""}, "$set": {"updated_at": now}},
        )
    else:
        result = await db.candidates.update_many(
            {"login": {"$in": cleaned}},
            {"$set": {"outreach_status": status, "updated_at": now}},
        )
    return int(result.modified_count)


@router.patch("/contacts/{login}/status")
async def update_contact_status(login: str, body: StatusUpdate, request: Request) -> dict:
    db = request.app.state.db
    candidate = await db.candidates.find_one({"login": login}, {"login": 1})
    if candidate is None:
        raise HTTPException(status_code=404, detail="contact not found")
    updated = await _apply_status(db, [login], body.status)
    return {"login": login, "outreach_status": body.status, "updated": updated}


@router.post("/contacts/status")
async def bulk_update_status(body: BulkStatus, request: Request) -> dict:
    updated = await _apply_status(request.app.state.db, body.logins, body.status)
    return {"updated": updated, "outreach_status": body.status}


@router.post("/contacts/export.csv")
async def export_contacts(body: ContactExport, request: Request) -> StreamingResponse:
    db = request.app.state.db
    labels = {
        document["_id"]: document.get("label") or document["_id"]
        for document in await _statuses(db)
    }

    async def lines():
        yield format_csv_row(COLUMNS)
        cursor = db.candidates.find({"login": {"$in": body.logins}}).sort(
            [("last_checked_at", -1), ("login", 1)]
        )
        candidates = await cursor.to_list(None)
        user_ids = [
            candidate["github_user_id"]
            for candidate in candidates
            if isinstance(candidate.get("github_user_id"), int)
        ]
        grouped = await _evidence_for(db, user_ids)
        for candidate in candidates:
            user_id = candidate.get("github_user_id")
            evidence = grouped.get(user_id, []) if isinstance(user_id, int) else []
            row = build_row(candidate, evidence, status_labels=labels)
            yield format_csv_row([row[column] for column in COLUMNS])

    return StreamingResponse(
        lines(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="fetchiq-contacts.csv"'},
    )
