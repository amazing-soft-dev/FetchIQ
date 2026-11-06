from typing import Literal

from pydantic import BaseModel


class OutreachStatusOut(BaseModel):
    id: str
    label: str
    position: int


class StatusList(BaseModel):
    statuses: list[OutreachStatusOut]


class Deleted(BaseModel):
    deleted: str


class ContactOut(BaseModel):
    login: str
    name: str | None
    profile_url: str
    public_email: str | None
    email_kinds: list[Literal["personal", "other"]]
    linkedin_url: str | None
    country: str | None
    outreach_status: str | None
    last_checked_at: str | None
    job_id: str | None


class ContactPageOut(BaseModel):
    contacts: list[ContactOut]
    total: int
    page: int
    page_size: int


class CountryList(BaseModel):
    countries: list[str]


class ContactLoginsOut(BaseModel):
    logins: list[str]
    total: int
    truncated: bool


class StatusChange(BaseModel):
    login: str
    outreach_status: str | None
    updated: int


class BulkStatusResult(BaseModel):
    updated: int
    outreach_status: str | None
