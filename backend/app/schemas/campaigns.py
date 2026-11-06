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
    status: CampaignStatus
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


class MailCheckOk(BaseModel):
    """The mailbox answered: who sends, through which host, and where copies go."""

    model_config = ConfigDict(populate_by_name=True)

    ok: Literal[True]
    from_address: str = Field(alias="from")
    host: str
    port: int
    sent_folder: str


class MailCheckFailed(BaseModel):
    ok: Literal[False]
    detail: str


# A union on ``ok`` so the generated TypeScript narrows the way the UI reads it.
MailCheckOut = MailCheckOk | MailCheckFailed
