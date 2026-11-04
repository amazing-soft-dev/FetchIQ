from collections import Counter
from pathlib import Path

from app.geo import COUNTRIES
from app.places import fold_place, load_places

EXCLUDED = (
    "barcelona",
    "bolivar",
    "lara",
    "leon",
    "merida",
    "miranda",
    "valencia",
)


def test_place_file_is_capped_and_attributed() -> None:
    catalog = load_places()
    assert catalog.version == 1
    counts = Counter(place.country for place in catalog.places)
    assert max(counts.values()) <= 30
    assert set(counts) <= set(COUNTRIES)

    seen: set[tuple[str, str]] = set()
    for place in catalog.places:
        key = (place.country, place.name.casefold())
        assert key not in seen
        seen.add(key)
        assert fold_place(place.name) != fold_place(place.country)
        assert fold_place(place.name) not in EXCLUDED
        assert place.kind in {"city", "region", "alias"}

    by_name = {(place.country, place.name): place for place in catalog.places}
    assert by_name[("Venezuela", "Caracas")].kind == "city"
    assert by_name[("Venezuela", "Caracas")].region
    assert by_name[("Venezuela", "Carabobo")].kind == "region"
    assert by_name[("Venezuela", "Vzla")].kind == "alias"
    assert by_name[("Germany", "Berlin")].kind == "city"
    assert by_name[("Germany", "Bayern")].kind == "region"
    assert by_name[("Germany", "Munich")].region == "Bavaria"
    assert by_name[("United States", "Austin")].region == "Texas"
    assert ("Spain", "Barcelona") not in by_name
    assert ("Spain", "Valencia") not in by_name


def test_readme_names_the_place_sources() -> None:
    text = Path(__file__).resolve().parents[2].joinpath("README.md").read_text(encoding="utf-8")
    assert "GeoNames" in text
    assert "CC BY 4.0" in text
    assert "countriesnow.space" in text
