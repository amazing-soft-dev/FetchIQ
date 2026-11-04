"""Map a published location string to a country and, for the US, a state.

Ambiguous strings such as London, California, and Remote stay unknown.
A city or region from the place file fills a country only when every
resolved part points at that same country. A region beats a city.
"""

from dataclasses import dataclass

from app.geo import UNITED_STATES, canonical_country, canonical_state
from app.places import Place, places_named

AMBIGUOUS = frozenset(
    {
        "africa",
        "anywhere",
        "apac",
        "asia",
        "california",
        "earth",
        "eu",
        "europe",
        "global",
        "home",
        "internet",
        "latam",
        "london",
        "n/a",
        "na",
        "none",
        "remote",
        "worldwide",
    }
)


@dataclass(frozen=True)
class NormalizedLocation:
    country: str | None
    us_state: str | None
    confidence: float


UNKNOWN = NormalizedLocation(country=None, us_state=None, confidence=0.0)


@dataclass(frozen=True)
class _Part:
    country: str
    us_state: str | None
    kind: str


def _clean(value: str) -> str:
    return " ".join(value.replace("·", " ").split()).strip(" ,;-")


def _us_state_from_place(place: Place) -> str | None:
    if place.country != UNITED_STATES:
        return None
    return canonical_state(place.region) or canonical_state(place.name)


def _from_place(part: str) -> _Part | None:
    matches = places_named(part)
    countries = {place.country for place in matches}
    if len(countries) != 1:
        return None
    regions = [place for place in matches if place.kind == "region"]
    chosen = regions[0] if regions else matches[0]
    kind = "region" if regions else chosen.kind
    return _Part(chosen.country, _us_state_from_place(chosen), kind)


def _from_part(part: str) -> _Part | None:
    """Right-hand tokens are read first by the caller. A country token wins."""

    country = canonical_country(part)
    if country is not None:
        return _Part(country, None, "country")
    state = canonical_state(part)
    if state is not None:
        return _Part(UNITED_STATES, state, "region")
    return _from_place(part)


def _from_parts(parts: list[str]) -> NormalizedLocation:
    hits = [hit for part in reversed(parts) if (hit := _from_part(part)) is not None]
    if not hits:
        return UNKNOWN
    countries = {hit.country for hit in hits}
    if len(countries) != 1:
        return UNKNOWN
    country = hits[0].country
    state = next((hit.us_state for hit in hits if hit.us_state), None)
    if country == UNITED_STATES and state is not None:
        return NormalizedLocation(country=country, us_state=state, confidence=0.7)
    if any(hit.kind == "region" for hit in hits):
        return NormalizedLocation(country=country, us_state=None, confidence=0.8)
    return NormalizedLocation(country=country, us_state=None, confidence=0.75)


def normalize_location(raw: str | None) -> NormalizedLocation:
    if raw is None or not raw.strip():
        return UNKNOWN
    text = _clean(raw)
    if not text:
        return UNKNOWN
    folded = text.casefold()
    if folded in AMBIGUOUS or ("," not in text and canonical_state(text) is not None):
        return UNKNOWN

    country = canonical_country(text)
    if country is not None and "," not in text:
        return NormalizedLocation(country=country, us_state=None, confidence=0.95)

    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) >= 2:
        country = canonical_country(parts[-1])
        if country is not None:
            state = canonical_state(parts[-2]) if country == UNITED_STATES else None
            if country == UNITED_STATES and state is not None:
                return NormalizedLocation(country=country, us_state=state, confidence=0.92)
            if country == UNITED_STATES:
                return NormalizedLocation(country=country, us_state=None, confidence=0.85)
            return NormalizedLocation(country=country, us_state=None, confidence=0.9)
        if len(parts[-1]) == 2:
            state = canonical_state(parts[-1])
            if state is not None:
                return NormalizedLocation(country=UNITED_STATES, us_state=state, confidence=0.7)
    return _from_parts(parts)
