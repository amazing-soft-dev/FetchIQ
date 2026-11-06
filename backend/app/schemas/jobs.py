from pydantic import BaseModel, ConfigDict

from app.models import JobStatus, SearchCriteria


class RequestBudget(BaseModel):
    limit: int | None
    remaining: int | None
    reset_at: str | None
    retry_after_seconds: float | None


class JobCounts(BaseModel):
    discovered: int
    enriched: int
    errors: int
    emails: int
    linkedin_without_email: int


class OverlapJob(BaseModel):
    id: str
    countries: list[str]
    live: int
    matched: int
    fresh: int


class OverlapPreview(BaseModel):
    fresh: int
    jobs: list[OverlapJob]


class JobOut(BaseModel):
    id: str
    status: JobStatus
    criteria: SearchCriteria
    incomplete: bool
    incomplete_reasons: list[str]
    counts: JobCounts
    request_budget: RequestBudget
    error: str | None
    overlap_preview: OverlapPreview | None
    created_at: str | None
    updated_at: str | None


class JobSummary(BaseModel):
    id: str
    status: JobStatus
    created_at: str | None


class JobList(BaseModel):
    jobs: list[JobSummary]


class PlaceReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    location: str | None
    reported: int | None
    fetched: int | None
    inserted: int | None
    updated: int | None
    duplicates: int | None
    status: str


class SliceCap(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)

    location: str | None = None
    start: str | None = None
    end: str | None = None
    reason: str
    reported: int | None = None
    fetched: int | None = None


class DateWindow(BaseModel):
    start: str
    end: str


class AnalysisOut(BaseModel):
    stop_reason: str
    headline: str
    limit: int
    live: int
    inserted: int
    updated: int | None
    duplicates: int | None
    slots: int
    shortfall: int
    moved_out: int | None
    window: DateWindow | None
    places_known: int
    places_unknown: int
    places: list[PlaceReport]
    caps: list[SliceCap]
    already_saved: list[OverlapJob]
    covered_countries: list[str]
    uncollected_countries: list[str]
    suggestions: list[str]


class ReadyOut(BaseModel):
    status: str
    checks: dict[str, str]
