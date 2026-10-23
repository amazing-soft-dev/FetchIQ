"""Campaign routes. Creating a campaign returns before any SMTP connection."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.campaigns import ACTIVE, CampaignBusy, NoRecipients, present_campaign, queue_campaign
from app.db import utcnow
from app.mail import UnknownPlaceholder, unknown_placeholders

router = APIRouter(prefix="/api")


class CampaignCreate(BaseModel):
    logins: list[str] = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    hourly_limit: int = Field(default=30, ge=1, le=500)
    delay_min_seconds: int = Field(default=30, ge=0, le=3600)
    delay_max_seconds: int = Field(default=60, ge=0, le=3600)

    @field_validator("subject", "body")
    @classmethod
    def strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("logins")
    @classmethod
    def clean_logins(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for login in value:
            item = login.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            cleaned.append(item)
        if not cleaned:
            raise ValueError("select at least one contact")
        if len(cleaned) > 20000:
            raise ValueError("too many contacts")
        return cleaned

    @model_validator(mode="after")
    def delay_order(self):
        if self.delay_min_seconds > self.delay_max_seconds:
            raise ValueError("minimum delay cannot exceed the maximum")
        return self


async def _load(db, campaign_id: str) -> dict:
    document = await db.campaigns.find_one({"_id": campaign_id})
    if document is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    return document


@router.get("/campaigns/active")
async def active_campaign(request: Request) -> dict:
    document = await request.app.state.db.campaigns.find_one(
        {"status": {"$in": list(ACTIVE)}},
        sort=[("created_at", -1)],
    )
    if document is None:
        return {"campaign": None}
    return {"campaign": await present_campaign(request.app.state.db, document)}


@router.post("/campaigns", status_code=201)
async def create_campaign(body: CampaignCreate, request: Request) -> dict:
    if not request.app.state.mailer.configured():
        raise HTTPException(status_code=422, detail="Mail is not configured.")
    try:
        names = unknown_placeholders(body.subject, body.body)
        if names:
            raise UnknownPlaceholder(names)
        document = await queue_campaign(request.app.state.db, body)
        await request.app.state.campaigns.kick(document["_id"])
    except UnknownPlaceholder as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NoRecipients as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CampaignBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    stored = await _load(request.app.state.db, document["_id"])
    return await present_campaign(request.app.state.db, stored)


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str, request: Request) -> dict:
    db = request.app.state.db
    return await present_campaign(db, await _load(db, campaign_id))


@router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: str, request: Request) -> dict:
    db = request.app.state.db
    document = await _load(db, campaign_id)
    if document["status"] == "paused":
        return await present_campaign(db, document)
    if document["status"] not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="campaign cannot be paused")
    if document["status"] == "queued":
        await db.campaigns.update_one(
            {"_id": campaign_id},
            {"$set": {"status": "paused", "control": None, "updated_at": utcnow()}},
        )
    else:
        await db.campaigns.update_one(
            {"_id": campaign_id},
            {"$set": {"control": "pause", "updated_at": utcnow()}},
        )
    return await present_campaign(db, await _load(db, campaign_id))


@router.post("/campaigns/{campaign_id}/resume")
async def resume_campaign(campaign_id: str, request: Request) -> dict:
    db = request.app.state.db
    document = await _load(db, campaign_id)
    if document["status"] != "paused":
        raise HTTPException(status_code=409, detail="campaign cannot be resumed")
    try:
        await request.app.state.campaigns.kick(campaign_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await present_campaign(db, await _load(db, campaign_id))


@router.post("/campaigns/{campaign_id}/cancel")
async def cancel_campaign(campaign_id: str, request: Request) -> dict:
    db = request.app.state.db
    document = await _load(db, campaign_id)
    if document["status"] in {"succeeded", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="campaign cannot be cancelled")
    if document["status"] == "running":
        await db.campaigns.update_one(
            {"_id": campaign_id},
            {"$set": {"control": "cancel", "updated_at": utcnow()}},
        )
    else:
        await db.campaigns.update_one(
            {"_id": campaign_id},
            {"$set": {"status": "cancelled", "control": None, "updated_at": utcnow()}},
        )
    return await present_campaign(db, await _load(db, campaign_id))
