"""Search criteria shared by jobs and presets."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.geo import UNITED_STATES, canonical_country, canonical_state

JobStatus = Literal[
    "queued",
    "running",
    "paused",
    "incomplete",
    "succeeded",
    "failed",
    "cancelled",
]

JobKind = Literal["github", "hackernews"]


def clean_keywords(raw_keywords: list[str]) -> list[str]:
    """Trim, drop blanks and repeats (case-insensitively), cap length and count."""

    keywords: list[str] = []
    seen: set[str] = set()
    for raw in raw_keywords:
        text = " ".join(raw.split())
        if not text:
            continue
        if len(text) > 50:
            raise ValueError("keywords must be 50 characters or fewer")
        folded = text.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        keywords.append(text)
    if len(keywords) > 20:
        raise ValueError("at most 20 keywords")
    return keywords


def clean_countries(raw_countries: list[str]) -> list[str]:
    countries: list[str] = []
    for raw in raw_countries:
        name = canonical_country(raw)
        if name is None:
            raise ValueError(f"unknown country: {raw}")
        if name not in countries:
            countries.append(name)
    if len(countries) > 25:
        raise ValueError("at most 25 countries")
    return countries


class HackerNewsCriteria(BaseModel):
    """What a Hacker News search reads and keeps.

    ``months_back`` is how many "Who wants to be hired?" threads to read, newest
    first. The filters are optional: empty ``countries`` and ``keywords`` and a
    false ``remote_only`` keep every post that has a contact. ``github_lookup``
    reads the GitHub profile a post links when the post has no personal email.
    """

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    months_back: int = Field(default=1, ge=1, le=24)
    countries: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    remote_only: bool = False
    github_lookup: bool = True

    @model_validator(mode="after")
    def normalize(self) -> "HackerNewsCriteria":
        self.countries = clean_countries(self.countries)
        self.keywords = clean_keywords(self.keywords)
        return self


class AccountAge(BaseModel):
    # Every field is present on the way out, so the response schema (and the
    # TypeScript generated from it) marks them required; the request schema keeps
    # its defaults.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    min_years: int = Field(default=0, ge=0, le=100)
    min_months: int = Field(default=0, ge=0, le=11)
    max_years: int = Field(default=5, ge=0, le=100)
    max_months: int = Field(default=0, ge=0, le=11)

    @model_validator(mode="after")
    def min_not_above_max(self) -> "AccountAge":
        minimum = self.min_years * 12 + self.min_months
        maximum = self.max_years * 12 + self.max_months
        if minimum > maximum:
            raise ValueError("min account age must be less than or equal to max account age")
        return self


class SearchCriteria(BaseModel):
    # Every field is present on the way out, so the response schema (and the
    # TypeScript generated from it) marks them required; the request schema keeps
    # its defaults.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    countries: list[str] = Field(default_factory=list)
    us_states: list[str] = Field(default_factory=list)
    account_age: AccountAge = Field(default_factory=AccountAge)
    keywords: list[str] = Field(default_factory=list)
    candidate_limit: int = Field(default=100, ge=1, le=5000)

    @model_validator(mode="after")
    def normalize_places(self) -> "SearchCriteria":
        countries = clean_countries(self.countries)
        if not countries:
            raise ValueError("select at least one country")

        states: list[str] = []
        for raw in self.us_states:
            name = canonical_state(raw)
            if name is None:
                raise ValueError(f"unknown US state: {raw}")
            if name not in states:
                states.append(name)
        if states and UNITED_STATES not in countries:
            raise ValueError("US states require United States")
        if UNITED_STATES not in countries:
            states = []

        self.countries = countries
        self.us_states = states
        self.keywords = clean_keywords(self.keywords)
        return self
