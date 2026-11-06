from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CampaignStatus = Literal["queued", "running", "paused", "succeeded", "failed", "cancelled"]


class CampaignCounts(BaseModel):
    queued: int
    sent: int
    failed: int
    skipped: int


class CampaignFailure(BaseModel):
    login: str
    error: str | None


class CampaignOut(BaseModel):
    id: str
    status: CampaignStatus | None
    subject: str
    body: str
    hourly_limit: int
    delay_min_seconds: int
    delay_max_seconds: int
    counts: CampaignCounts
    failures: list[CampaignFailure]
    error: str | None
    created_at: str | None
    updated_at: str | None


class ActiveCampaign(BaseModel):
    campaign: CampaignOut | None


class MailCheckOut(BaseModel):
    """``ok`` with the mailbox named, or ``ok: false`` with a ``detail``."""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    detail: str | None = None
    from_address: str | None = Field(default=None, alias="from")
    host: str | None = None
    port: int | None = None
    sent_folder: str | None = None
