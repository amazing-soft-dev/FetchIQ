"""Saved search criteria and the location catalog for the UI."""

from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from app.db import utcnow
from app.geo import COUNTRIES, US_STATES
from app.models import SearchCriteria

router = APIRouter(prefix="/api")


class PresetCreate(BaseModel):
    name: str
    criteria: SearchCriteria

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        text = " ".join(value.split())
        if not text or len(text) > 80:
            raise ValueError("preset name must be 1 to 80 characters")
        return text


def public_preset(document: dict) -> dict:
    created = document.get("created_at")
    return {
        "id": document["_id"],
        "name": document["name"],
        "criteria": document["criteria"],
        "created_at": created.isoformat() if created is not None else None,
    }


@router.get("/geo")
async def geo() -> dict[str, list[str]]:
    return {"countries": list(COUNTRIES), "us_states": list(US_STATES)}


@router.post("/presets", status_code=201)
async def create_preset(body: PresetCreate, request: Request) -> dict:
    document = {
        "_id": str(uuid4()),
        "name": body.name,
        "criteria": body.criteria.model_dump(),
        "created_at": utcnow(),
    }
    await request.app.state.db.search_presets.insert_one(document)
    return public_preset(document)


@router.get("/presets")
async def list_presets(request: Request) -> dict:
    cursor = request.app.state.db.search_presets.find({}).sort("created_at", -1)
    documents = await cursor.to_list(100)
    return {"presets": [public_preset(document) for document in documents]}


@router.get("/presets/{preset_id}")
async def get_preset(preset_id: str, request: Request) -> dict:
    document = await request.app.state.db.search_presets.find_one({"_id": preset_id})
    if document is None:
        raise HTTPException(status_code=404, detail="preset not found")
    return public_preset(document)
