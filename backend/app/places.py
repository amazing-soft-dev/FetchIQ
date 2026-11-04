"""Cities and regions shipped with the app.

``backend/scripts/build_places.py`` writes the JSON file from GeoNames and
countriesnow.space. The running app only reads that file.
"""

import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).with_name("data") / "places.json"

KIND_ORDER = {"city": 0, "region": 1, "alias": 2}


def fold_place(value: str) -> str:
    """Case-fold and strip accents so Mérida and Merida share one key."""

    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_marks.casefold().split())


@dataclass(frozen=True)
class Place:
    country: str
    name: str
    kind: str
    region: str
    rank: int


@dataclass(frozen=True)
class PlaceCatalog:
    version: int
    places: tuple[Place, ...]


@lru_cache(maxsize=1)
def load_places() -> PlaceCatalog:
    payload = json.loads(_PATH.read_text(encoding="utf-8"))
    places = tuple(
        Place(
            country=row["country"],
            name=row["name"],
            kind=row["kind"],
            region=row.get("region") or "",
            rank=int(row["rank"]),
        )
        for row in payload["places"]
    )
    return PlaceCatalog(version=int(payload["version"]), places=places)


def places_for_country(country: str) -> tuple[Place, ...]:
    catalog = load_places()
    return tuple(place for place in catalog.places if place.country == country)


@lru_cache(maxsize=1)
def _places_by_name() -> dict[str, tuple[Place, ...]]:
    grouped: dict[str, list[Place]] = {}
    for place in load_places().places:
        grouped.setdefault(fold_place(place.name), []).append(place)
    return {name: tuple(places) for name, places in grouped.items()}


def places_named(name: str) -> tuple[Place, ...]:
    """Places whose name matches after accent-folding. Empty when nothing matches."""

    return _places_by_name().get(fold_place(name), ())
