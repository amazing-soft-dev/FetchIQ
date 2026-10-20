"""Map a published location string to a country and, for the US, a state.

Ambiguous strings such as London, California, and Remote stay unknown.
"""

from dataclasses import dataclass

from app.geo import UNITED_STATES, canonical_country, canonical_state

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


def _clean(value: str) -> str:
    return " ".join(value.replace("·", " ").split()).strip(" ,;-")


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
    return UNKNOWN
