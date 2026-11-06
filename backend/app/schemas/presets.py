from pydantic import BaseModel

from app.models import SearchCriteria


class GeoCatalog(BaseModel):
    countries: list[str]
    us_states: list[str]


class PresetOut(BaseModel):
    id: str
    name: str
    criteria: SearchCriteria
    created_at: str | None


class PresetList(BaseModel):
    presets: list[PresetOut]
