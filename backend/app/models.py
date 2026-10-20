"""Search criteria shared by jobs and presets."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

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


class AccountAge(BaseModel):
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
    countries: list[str] = Field(default_factory=list)
    us_states: list[str] = Field(default_factory=list)
    account_age: AccountAge = Field(default_factory=AccountAge)
    keywords: list[str] = Field(default_factory=list)
    candidate_limit: int = Field(default=100, ge=1, le=5000)

    @model_validator(mode="after")
    def normalize_places(self) -> "SearchCriteria":
        countries: list[str] = []
        for raw in self.countries:
            name = canonical_country(raw)
            if name is None:
                raise ValueError(f"unknown country: {raw}")
            if name not in countries:
                countries.append(name)
        if not countries:
            raise ValueError("select at least one country")
        if len(countries) > 25:
            raise ValueError("at most 25 countries")

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

        keywords: list[str] = []
        seen: set[str] = set()
        for raw in self.keywords:
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

        self.countries = countries
        self.us_states = states
        self.keywords = keywords
        return self
